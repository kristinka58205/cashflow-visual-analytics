from django.contrib import admin
from .models import BankAccount, CashCategory, CashTransaction

admin.site.register([BankAccount, CashCategory, CashTransaction])
