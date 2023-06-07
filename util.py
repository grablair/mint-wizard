from decimal import Decimal
import re

def money_str_to_decimal(money_str):
	return Decimal(re.sub(r'[\$,]', '', money_str))