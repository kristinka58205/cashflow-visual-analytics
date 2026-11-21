from django.contrib import admin
from .models import BankAccount, CashCategory, CashTransaction, Counterparty, CashProject

admin.site.register([BankAccount, CashCategory, CashTransaction, Counterparty, CashProject])
