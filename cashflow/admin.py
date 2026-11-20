from django.contrib import admin
from .models import BankAccount, CashCategory

admin.site.register([BankAccount, CashCategory])
