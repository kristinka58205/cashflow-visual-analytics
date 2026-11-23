"""Воспроизводимый количественный срез, а не оценка комиссии."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.apps import apps
from cashflow.public import GUIDES
from cashflow.cabinet import PAGES


def audit():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    counts=[]
    for name in filter(None,files):
        path=ROOT/name
        if not name.startswith(('cashflow/','config/','templates/')) or any(part in {'migrations','tests'} for part in path.parts) or path.name.startswith('test'):continue
        if path.suffix not in {'.py','.html','.css'}:continue
        content=path.read_text()
        if path.suffix=='.py':
            tree=ast.parse(content)
            count=sum(isinstance(node,ast.stmt) and not (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str)) for node in ast.walk(tree))
            metric='python_ast_statements'
        else:
            content=re.sub(r'<!--.*?-->|{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|/\*.*?\*/','',content,flags=re.S)
            count=sum(bool(line.strip()) for line in content.splitlines());metric='nonempty_markup_style_lines'
        counts.append({'file':name,'metric':metric,'count':count,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    models=[{'model':m.__name__,'table':m._meta.db_table,'fields':[f.name for f in m._meta.local_fields]} for m in apps.get_app_config('cashflow').get_models()]
    technical={'DemoDataset'}
    result={'schema_version':1,'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'code_counts':counts,'mixed_code_units':sum(row['count'] for row in counts),
        'method':'Python AST statements excluding docstrings/tests/migrations; HTML/CSS nonempty lines excluding comments. Different units are reported separately; not a standardized logical-line certification.',
        'application_models':models,'domain_table_count':sum(row['model'] not in technical for row in models),'technical_models':sorted(technical),
        'public_guides':[{'path':'/guide/'+row[0]+'/','title':row[1]} for row in GUIDES],
        'staff_pages':[{'path':'/cabinet/'+slug+'/','title':title} for slug,title in PAGES]+[{'path':'/cabinet/feedback/','title':'Обращения'}],
        'private_pages':['/operations/','/catalog/','/analytics/','/budgets/','/payments/','/organizations/','/history/','/reconciliation/','/forecast/','/transfers/'],
        'roles':['admin','editor','viewer'],'limitations':['Counts describe source structure, not semantic correctness. Formal logical-line counting and report formatting require the supervisor’s accepted method.']}
    target=ROOT/'reports/requirements-audit-v1.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['mixed_code_units','domain_table_count','technical_models']},ensure_ascii=False))


if __name__=='__main__':audit()
