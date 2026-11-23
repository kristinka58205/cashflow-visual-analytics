"""Реальный Chrome в изолированной копии приложения с вымышленными данными."""
import argparse
import csv
import io
from datetime import date
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from playwright.sync_api import sync_playwright


def verify(app_python, output):
    root = Path(__file__).resolve().parents[1]
    app_python = Path(os.path.abspath(app_python))
    output.mkdir(parents=True, exist_ok=True)
    names = subprocess.check_output(['git','ls-files','-z'], cwd=root).decode().split('\0')
    password = secrets.token_urlsafe(24)
    env = {k:v for k,v in os.environ.items() if k not in {'DATABASE_URL','SECRET_KEY','DJANGO_SETTINGS_MODULE','PYTHONPATH','PYTHONHOME'}}
    env.update(DEBUG='1',ALLOWED_HOSTS='127.0.0.1,localhost,testserver')
    checks=[]; screenshots=[]; sources={}
    with tempfile.TemporaryDirectory(prefix='cashflow-browser-') as directory:
        target=Path(directory)
        for name in filter(None,names):
            original=root/name
            if original.is_symlink():raise RuntimeError('Symlink in source kit')
            destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(original,destination);sources[name]=hashlib.sha256(original.read_bytes()).hexdigest()
        def run(*args,input=None):
            return subprocess.run([str(app_python),*args],cwd=target,env=env,input=input,text=True,
                capture_output=True,check=True,timeout=90).stdout
        run('main.py','migrate','--noinput')
        seed="""import json,sys; from django.contrib.auth import get_user_model; from cashflow.demo import seed_demo; from cashflow.models import BankAccount,CashCategory; u=get_user_model().objects.create_user('browser-demo',password=json.load(sys.stdin)['password']); seed_demo(u); b=BankAccount.objects.create(owner=u,name='Демо: второй счёт'); print(json.dumps({'first':BankAccount.objects.filter(owner=u).exclude(pk=b.pk).get().pk,'second':b.pk,'income':CashCategory.objects.get(owner=u,kind='income').pk}))"""
        ids=json.loads(run('main.py','shell','-c',seed,input=json.dumps({'password':password})).splitlines()[-1])
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        with (target/'server.log').open('w') as log:
            server=subprocess.Popen([str(app_python),str(target/'main.py'),'runserver',f'127.0.0.1:{port}','--noreload'],cwd=target,env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+20
                while True:
                    try:
                        with urlopen(url,timeout=2) as response:assert response.status==200
                        break
                    except (URLError,TimeoutError):
                        if server.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Local server failed')
                        time.sleep(.1)
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='chrome',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000},locale='ru-RU',timezone_id='Asia/Tbilisi',accept_downloads=True)
                    errors=[];page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    def visit(path):
                        response=page.goto(url+path);assert response.status==200,(path,response.status)
                    def shot(name):
                        page.screenshot(path=str(output/name),full_page=True);screenshots.append(name)
                    visit('/accounts/login/');page.locator('#id_username').fill('browser-demo');page.locator('#id_password').fill(password)
                    page.locator('main form button').click();page.wait_for_url(url+'/');checks.append('real_form_login')
                    shot('dashboard-desktop.png')
                    visit('/operations/new/')
                    page.locator('#id_account').select_option(str(ids['first']));page.locator('#id_category').select_option(str(ids['income']))
                    page.locator('#id_occurred_on').fill(date.today().isoformat());page.locator('#id_amount').fill('0.10')
                    page.locator('#id_description').fill('Браузерная проверка точных копеек')
                    repeated=page.locator('main form').evaluate('(form)=>Object.fromEntries(new FormData(form))')
                    page.get_by_role('button',name='Сохранить',exact=True).click()
                    page.wait_for_url('**/operations/');assert 'Браузерная проверка точных копеек' in page.locator('main').inner_text();checks.append('manual_operation_saved')
                    replay=context.request.post(url+'/operations/new/',form=repeated,headers={'Referer':url+'/operations/new/'})
                    assert replay.status==200
                    page.reload()
                    assert page.locator('article.task').filter(has_text='Браузерная проверка точных копеек').count()==1
                    checks.append('manual_operation_retry_keeps_one_row')
                    visit('/transfers/');page.locator('#id_from_account').select_option(str(ids['first']));page.locator('#id_to_account').select_option(str(ids['second']))
                    page.locator('#id_amount').fill('0.05');page.locator('#id_occurred_on').fill(date.today().isoformat());page.locator('#id_description').fill('Перевод браузерной проверки')
                    page.get_by_role('button',name='Записать перевод').click();page.wait_for_url('**/transfers/')
                    assert 'Перевод браузерной проверки' in page.locator('tbody').inner_text();checks.append('transfer_saved');shot('transfers-desktop.png')
                    visit('/analytics/');shot('analytics-desktop.png')
                    with page.expect_download() as result:
                        page.get_by_role('link',name='Скачать PDF-отчёт').click()
                    artifact=output/'cashflow-browser.pdf';result.value.save_as(artifact)
                    assert artifact.read_bytes().startswith(b'%PDF-');checks.append('browser_pdf_download')
                    for path in ['/catalog/','/payments/','/budgets/','/history/','/reconciliation/','/forecast/']:
                        visit(path)
                    checks.append('authenticated_navigation')
                    visit('/reconciliation/')
                    assert page.locator('#id_checked_on').input_value()==date.today().isoformat()
                    page.locator('#id_account').select_option(str(ids['first']))
                    page.locator('#id_statement_balance').fill('188750.50')
                    page.get_by_role('button',name='Сохранить сверку',exact=True).click();page.wait_for_url('**/reconciliation/')
                    assert 'Остатки совпадают' in page.locator('main').inner_text()
                    checks.append('dated_reconciliation_form_saved');shot('reconciliation-desktop.png')
                    visit('/statement/')
                    page.locator('#id_account').select_option(str(ids['first']))
                    page.get_by_role('button',name='Сформировать выписку',exact=True).click()
                    assert '188750,50' in page.locator('main').inner_text()
                    with page.expect_download() as result:page.get_by_role('link',name='Скачать полную выписку CSV',exact=True).click()
                    statement=target/'statement.csv';result.value.save_as(statement)
                    rows=list(csv.DictReader(io.StringIO(statement.read_text(encoding='utf-8-sig')),delimiter=';'))
                    assert rows[0]['closing']=='188750.50' and any(row['source']=='transfer' for row in rows)
                    checks.append('account_statement_and_full_csv');shot('statement-desktop.png')
                    visit('/analytics/')
                    page.get_by_role('link',name='Потоки по проектам и контрагентам',exact=True).click()
                    assert '88750,55' in page.locator('main').inner_text()
                    with page.expect_download() as result:page.get_by_role('link',name='Скачать все группы CSV',exact=True).click()
                    dimensions_csv=target/'dimensions.csv';result.value.save_as(dimensions_csv)
                    groups=list(csv.DictReader(io.StringIO(dimensions_csv.read_text(encoding='utf-8-sig')),delimiter=';'))
                    assert groups[0]['net']=='88750.55' and len(groups)==3
                    shot('dimensions-desktop.png')
                    page.get_by_role('link',name='Без проекта',exact=True).click()
                    assert page.locator('article.task').count()==1
                    checks.append('dimension_totals_csv_and_unassigned_drilldown')
                    visit('/balances/')
                    assert '188750,55' in page.locator('main').inner_text()
                    with page.expect_download() as result:page.get_by_role('link',name='Скачать все счета CSV',exact=True).click()
                    positions_csv=target/'positions.csv';result.value.save_as(positions_csv)
                    positions=list(csv.DictReader(io.StringIO(positions_csv.read_text(encoding='utf-8-sig')),delimiter=';'))
                    assert positions[0]['balance']=='188750.55' and len(positions)==3
                    checks.append('all_account_positions_and_csv');shot('positions-desktop.png')
                    for _ in range(2):
                        visit('/operations/new/')
                        page.locator('#id_account').select_option(str(ids['first']));page.locator('#id_category').select_option(str(ids['income']))
                        page.locator('#id_occurred_on').fill(date.today().isoformat());page.locator('#id_amount').fill('0.01')
                        page.locator('#id_description').fill('Браузерное совпадение для проверки')
                        page.get_by_role('button',name='Сохранить',exact=True).click();page.wait_for_url('**/operations/')
                    visit('/quality/')
                    assert 'совпадений: 2' in page.locator('main').inner_text()
                    with page.expect_download() as result:page.get_by_role('link',name='Скачать все совпадения CSV',exact=True).click()
                    duplicates_csv=target/'duplicates.csv';result.value.save_as(duplicates_csv)
                    duplicates=list(csv.DictReader(io.StringIO(duplicates_csv.read_text(encoding='utf-8-sig')),delimiter=';'))
                    assert len(duplicates)==1 and duplicates[0]['matching_records']=='2' and duplicates[0]['possible_extra']=='0.01'
                    checks.append('possible_duplicates_from_two_distinct_forms');shot('quality-desktop.png')
                    page.set_viewport_size({'width':390,'height':844})
                    for path,name in [('/operations/','operations-mobile.png'),('/transfers/','transfers-mobile.png'),('/analytics/dimensions/','dimensions-mobile.png'),('/balances/','positions-mobile.png'),('/quality/','quality-mobile.png'),('/forecast/?account='+str(ids['first'])+'&days=7','forecast-mobile.png')]:
                        visit(path);shot(name)
                        dimensions=page.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
                        assert dimensions['scroll']<=dimensions['width']+1,(path,dimensions)
                        assert page.locator('.guide-menu').evaluate('(el)=>el.getBoundingClientRect().height')<400
                    checks.append('mobile_390_layout');assert not errors,errors;checks.append('no_browser_page_errors')
                    browser_version=browser.version;context.close();browser.close()
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
    report={'status':'passed','browser':'Chrome '+browser_version,'playwright':version('playwright'),
        'source_sha256':sources,'checks':checks,'screenshots':screenshots,'viewports':[[1440,1000],[390,844]],
        'limitations':['Simulated personal ledger and one browser; not a complete accessibility or cross-browser audit.']}
    (output/'browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Chrome acceptance passed:',len(checks),'checks')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-python',default='.venv/bin/python')
    parser.add_argument('--output',type=Path,default=Path('reports/local-visual-v1'))
    args=parser.parse_args();verify(args.app_python,args.output.resolve())
