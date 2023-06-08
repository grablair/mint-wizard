from recurrent.event_parser import RecurringEvent
from dateutil import rrule
from decimal import Decimal
import re

def money_str_to_decimal(money_str):
	return Decimal(re.sub(r'[\$,]', '', money_str))

def str_to_valid_recurring_event(rule):
	r = RecurringEvent()
	r.parse(rule)

	if not r.is_recurring:
		raise ValueError(f"The given recurrence rule is invalid: {rule}")

	return r