from recurrent.event_parser import RecurringEvent
from datetime import datetime
from dateutil import rrule
from decimal import Decimal
import re

def money_str_to_decimal(money_str):
	return Decimal(re.sub(r'[\$,]', '', money_str))

def rrule_for_txn(txn):
	return normalize_rfc_rule(txn.recurring_event.get_RFC_rrule(), txn.previous_occurrence)

def normalize_rfc_rule(rfc_rule, default_start_date=datetime.now()):
	if "DTSTART" not in rfc_rule:
		rfc_rule = f"DTSTART:{default_start_date.strftime('%Y%m%dT%H%M%S')}\n{rfc_rule}"
	if "BYHOUR" not in rfc_rule:
		rfc_rule = f"{rfc_rule};BYHOUR=0"
	if "BYMINUTE" not in rfc_rule:
		rfc_rule = f"{rfc_rule};BYMINUTE=0"
	if "BYSECOND" not in rfc_rule:
		rfc_rule = f"{rfc_rule};BYSECOND=0"

	return rfc_rule

def str_to_valid_recurring_event(rule):
	r = RecurringEvent()
	r.parse(rule)

	normalized_rule = normalize_rfc_rule(r.get_RFC_rrule())

	r.parse(r.format(normalized_rule))

	if not r.is_recurring:
		raise ValueError(f"The given recurrence rule is invalid: {rule}")

	return r