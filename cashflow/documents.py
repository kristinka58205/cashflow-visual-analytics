"""Финансовый PDF из согласованного снимка текущей рабочей области."""
from contextlib import contextmanager
from html import escape
from io import BytesIO
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from .access import LedgerScope, get_scope
from .forms import AnalysisPeriodForm
from .models import Organization
from .services import cashflow_analysis


@contextmanager
def report_scope(user, scope):
    if user.pk != scope.user.pk:
        raise PermissionDenied
    with transaction.atomic():
        organization = None
        if scope.organization_id:
            try:
                organization = Organization.objects.select_for_update().get(pk=scope.organization_id)
            except Organization.DoesNotExist:
                raise PermissionDenied from None
        locked = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not locked.is_active:
            raise PermissionDenied
        yield LedgerScope(user, organization)


def render_cashflow_pdf(scope_name, start, end, report):
    fonts = Path(__file__).parent/"fonts"
    for name,filename in [("CashflowSans","DejaVuSans.ttf"),("CashflowBold","DejaVuSans-Bold.ttf")]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name,str(fonts/filename)))
    body = ParagraphStyle("body",fontName="CashflowSans",fontSize=9,leading=13,spaceAfter=7)
    heading = ParagraphStyle("heading",parent=body,fontName="CashflowBold",fontSize=18,leading=23,spaceAfter=12)
    small = ParagraphStyle("small",parent=body,fontSize=7.5,leading=10)
    money_style = ParagraphStyle("money",parent=small,alignment=TA_RIGHT)
    def p(value,style=body):
        return Paragraph(escape(str(value)),style)
    def money(value):
        return p(f"{value:.2f}".replace(".",","),money_style)
    def table(rows,widths):
        result=Table(rows,colWidths=[width*mm for width in widths],repeatRows=1,hAlign="LEFT")
        result.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e9eff6")),("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,0),(-1,0),.6,colors.HexColor("#8393a7")),("BOTTOMPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6),("LINEBELOW",(0,1),(-1,-1),.2,colors.HexColor("#dbe2eb"))]))
        return result
    stream=BytesIO()
    document=SimpleDocTemplate(stream,pagesize=A4,leftMargin=17*mm,rightMargin=17*mm,topMargin=18*mm,bottomMargin=24*mm,title="Отчёт о денежных потоках",author="Жукова Кристина Дмитриевна")
    content=[p("Отчёт о денежных потоках",heading),p(scope_name),p(f"Период: {start:%d.%m.%Y} - {end:%d.%m.%Y}"),p("Валюта: рубли. Сформировано: "+timezone.localtime().strftime("%d.%m.%Y %H:%M %Z"),small)]
    summary=report["summary"]
    content.append(table([[p("Показатель",small),p("Сумма, руб.",small)],*[ [p(label,small),money(summary[key])] for key,label in [("opening","Остаток на начало периода"),("income","Поступления"),("expense","Расходы"),("net","Чистый денежный поток"),("closing","Остаток на конец периода")]]],[110,66]))
    content.extend([Spacer(1,10*mm),p("Динамика по месяцам",body)])
    rows=[[p(value,small) for value in ["Месяц","Поступления","Расходы","Чистый поток","Остаток"]]]
    for row in report["months"]:
        rows.append([p(row["month"].strftime("%m.%Y"),small),*[money(row[key]) for key in ["income","expense","net","closing"]]])
    content.append(table(rows,[23,38.25,38.25,38.25,38.25]))
    for kind,title in [("income","Статьи поступлений"),("expense","Статьи расходов")]:
        items=[item for item in report["categories"] if item["kind"]==kind]
        if not items:continue
        content.extend([Spacer(1,8*mm),p(title,body)])
        rows=[[p(value,small) for value in ["Статья","Сумма, руб.","Доля направления"]]]
        rows.extend([[p(item["name"],small),money(item["amount"]),p(f"{item['share']:.2f}%",money_style)] for item in items])
        content.append(table(rows,[98,42,36]))
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont("CashflowSans",7)
        canvas.setFont("CashflowSans",6.5)
        canvas.drawString(17*mm,18*mm,"Отчёт отражает введённые движения денег и не является бухгалтерской отчётностью.")
        canvas.drawString(17*mm,15*mm,"Начальный остаток включает движения до периода; доли статей считаются по направлению.")
        canvas.setFont("CashflowSans",7)
        canvas.drawString(17*mm,9*mm,"Поток | Жукова Кристина Дмитриевна")
        canvas.drawRightString(A4[0]-17*mm,9*mm,"Страница "+str(doc.page));canvas.restoreState()
    document.build(content,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


@login_required
def cashflow_pdf(request):
    today=timezone.localdate()
    form=AnalysisPeriodForm(request.GET if request.GET else {"start":today.replace(day=1),"end":today})
    if not form.is_valid():
        return HttpResponseBadRequest("Укажите корректный период отчёта.")
    with report_scope(request.user,get_scope(request)) as scope:
        report=cashflow_analysis(request.user,scope=scope,**form.cleaned_data)
        if len(report["categories"])>500:
            return HttpResponseBadRequest("Слишком много статей: уточните период отчёта.")
        payload=render_cashflow_pdf(scope.name,form.cleaned_data["start"],form.cleaned_data["end"],report)
    response=HttpResponse(payload,content_type="application/pdf")
    response["Content-Disposition"]='attachment; filename="cashflow-report.pdf"'
    response["Cache-Control"]="private, no-store"
    return response
