import sqlite3

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, select, insert
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from dateutil import rrule
from dateutil.rrule import YEARLY
from typing import Optional, List

from secrets import token_hex
from types import SimpleNamespace
from decimal import Decimal
from recurrent.event_parser import RecurringEvent

import json
import logging

from util import rrule_for_txn, normalize_rfc_rule

logger = logging.getLogger(__name__)

Base = declarative_base()

def get_next_occurrence_for_txn(txn):
	return rrule.rrulestr(rrule_for_txn(txn)).after(txn.previous_occurrence)

class RecurringEventType(TypeDecorator):
	impl = TEXT
	cache_ok = True

	def process_bind_param(self, value, dialect):
		if value is not None and isinstance(value, RecurringEvent) and value.is_recurring:
			value = value.get_RFC_rrule()
		return value

	def process_result_value(self, value, dialect):
		if value is not None:
			rfc_rule = normalize_rfc_rule(value)
			r = RecurringEvent()
			r.parse(r.format(rfc_rule))
			return r
		return value

class RecurringTransaction(Base):
	__tablename__ = 'recurring_transaction'

	id: Mapped[int] = mapped_column(primary_key=True)
	description: Mapped[str]
	amount: Mapped[str]
	category: Mapped[str]
	dedupe_string: Mapped[str]
	recurring_event: Mapped[str] = mapped_column(RecurringEventType)
	previous_occurrence: Mapped[datetime]
	notes: Mapped[Optional[str]]

	def __repr__(self) -> str:
		return f"RecurringTransaction(id={self.id!r}, description={self.description!r}, amount={self.amount!r}, category={self.category!r}, dedupe_string={self.dedupe_string!r}, recurring_event=RecurringEvent(rule='{self.recurring_event.format(rrule_for_txn(self))}'), previous_occurrence={self.previous_occurrence!r}, inferred_next_occurrence='{get_next_occurrence_for_txn(self)}')"

class Db:
	def __init__(self, db_path):
		self.engine = create_engine("sqlite:///%s" % db_path)
		Base.metadata.create_all(self.engine)

	def get_all_recurring_transactions(self):
		stmt = select(RecurringTransaction)
		with Session(self.engine) as session:
			return session.scalars(stmt).all()

	def create_recurring_transaction(self, description, amount_decimal, category, recurring_event):
		if not isinstance(recurring_event, RecurringEvent) or not recurring_event.is_recurring:
			raise ValueError("Event must be recurring, but is not.")

		if recurring_event.dtstart != None and recurring_event.dtstart <= datetime.now():
			logger.warn("Warning! Start date is in the past. A transaction will _not_ be created automatically for that start time.")

		txn = RecurringTransaction(
			description=description,
			amount=str(amount_decimal),
			category=category,
			dedupe_string=token_hex(8),
			recurring_event=recurring_event,
			previous_occurrence=datetime.now() if recurring_event.dtstart == None else min(datetime.now(), recurring_event.dtstart)
		)

		with Session(self.engine) as session:
			session.add(txn)
			session.commit()
			txn = session.get(RecurringTransaction, txn.id)
			logger.info(f"Created new recurring transaction: {txn}. Next occurrence: {get_next_occurrence_for_txn(txn)}")

	def remove_recurring_transaction(self, id):
		with Session(self.engine) as session:
			txn = session.get(RecurringTransaction, id)
			logger.info("Removing recurring transaction: %s" % txn)
			session.delete(txn)
			session.commit()
			logger.info("Removed recurring transaction")

	def get_past_due_recurring_transactions(self):
		def is_next_recurrence_before_now(txn):
			rules = rrule.rrulestr(rrule_for_txn(txn))
			return rules.after(txn.previous_occurrence) < datetime.now()

		self.clean_up_expired_recurring_transactions()
		return list(filter(is_next_recurrence_before_now, self.get_all_recurring_transactions()))

	def clean_up_expired_recurring_transactions(self):
		stmt = select(RecurringTransaction)

		with Session(self.engine) as session:
			for txn in session.scalars(stmt).all():
				rules = rrule.rrulestr(rrule_for_txn(txn))
				if not rules.after(txn.previous_occurrence):
					# if there is an end date, and if the final occurrence is equal to the previous one
					logger.info("Cleaning up expired recurring transaction %s" % txn)
					session.delete(txn)
			session.commit()

	def process_recurring_transaction_completion(self, id):
		with Session(self.engine) as session:
			txn = session.get(RecurringTransaction, id)
			new_occurrence = get_next_occurrence_for_txn(txn)
			next_occurrence = rrule.rrulestr(rrule_for_txn(txn)).after(new_occurrence)
			logger.info(f"updating previous occurrence for transaction \"{txn.description}\" from {txn.previous_occurrence} to {new_occurrence}. Next occurrence will be {next_occurrence}")
			txn.previous_occurrence = new_occurrence
			session.commit()

	def get_next_occurrence_for_txn_by_id(self, id):
		with Session(self.engine) as session:
			return get_next_occurrence_for_txn(session.get(RecurringTransaction, id))

	def schedule_single_transaction(self, description, amount_decimal, category, txn_date, dedupe, notes=None):
		now = datetime.now(tz=timezone.utc)

		if now > txn_date:
			logger.error(f"Invalid date for scheduling a transaction; the date must be in the past. Skipping. now: {now}; txn_date: {txn_date}")
			return

		rr = normalize_rfc_rule(rrule.rrule(
			freq=YEARLY,
			count=1,
			bymonth=txn_date.month,
			bymonthday=txn_date.day
		))

		recurring_event = RecurringEvent()
		recurring_event.parse(recurring_event.format(rr))

		txn = RecurringTransaction(
			description=description,
			amount=str(amount_decimal),
			category=category,
			dedupe_string=dedupe,
			recurring_event=recurring_event,
			previous_occurrence=now,
			notes=notes
		)

		sel_stmt = select(RecurringTransaction).where(RecurringTransaction.dedupe_string == dedupe)
		with Session(self.engine) as session:
			if session.execute(sel_stmt).first() is not None:
				logger.info("Duplicate scheduled transaction found: %s (dedupe string: %s). Skipping..." % (description, dedupe))
				return

			session.add(txn)
			session.commit()
			txn = session.get(RecurringTransaction, txn.id)
			logger.info(f"Scheduled new transaction: {txn}. Execution date: {get_next_occurrence_for_txn(txn)}")

