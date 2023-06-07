import sqlite3

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, select, insert
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Optional

from secrets import token_hex
from types import SimpleNamespace

import json
import logging

Base = declarative_base()

class RelativeDeltaType(TypeDecorator):

	impl = TEXT

	cache_ok = True

	def process_bind_param(self, value, dialect):
		if value is not None:
			delta_dict = {k:v for k,v in value.__dict__.items() if not k.startswith("_")}
			value = json.dumps(delta_dict)

		return value

	def process_result_value(self, value, dialect):
		if value is not None:
			value = json.loads(value, object_hook=lambda d: relativedelta(**d))
		return value

class RecurringTransaction(Base):
	__tablename__ = 'recurring_transaction'

	id: Mapped[int] = mapped_column(primary_key=True)
	description: Mapped[str]
	amount: Mapped[str]
	category: Mapped[str]
	frequency: Mapped[str] = mapped_column(RelativeDeltaType)
	dedupe_string: Mapped[str]
	next_occurrence: Mapped[datetime]
	stop_after: Mapped[Optional[datetime]]

	def __repr__(self) -> str:
		return f"RecurringTransaction(id={self.id!r}, description={self.description!r}, amount={self.amount!r}, category={self.category!r}, frequency={self.frequency!r}, dedupe_string={self.dedupe_string!r}, next_occurrence={self.next_occurrence!r}, stop_after={self.stop_after!r})"

class Db:
	def __init__(self, db_path):
		self.engine = create_engine("sqlite:///%s" % db_path)
		Base.metadata.create_all(self.engine)

	def get_all_recurring_transactions(self):
		stmt = select(RecurringTransaction)
		with Session(self.engine) as session:
			return session.scalars(stmt).all()

	def create_recurring_transaction(self, description, amount_decimal, category, frequency, first_occurrence, stop_after):
		txn = RecurringTransaction(description=description, amount=str(amount_decimal), category=category, frequency=frequency, dedupe_string=token_hex(8), next_occurrence=first_occurrence, stop_after=stop_after)
		with Session(self.engine) as session:
			session.add(txn)
			session.commit()
			logging.info("Created new recurring transaction: %s" % txn)

	def remove_recurring_transaction(self, id):
		with Session(self.engine) as session:
			txn = session.get(RecurringTransaction, id)
			logging.info("Removing recurring transaction: %s" % txn)
			session.delete(txn)
			session.commit()
			logging.info("Removed recurring transaction")

	def get_past_due_recurring_transactions(self):
		self.clean_up_expired_recurring_transactions()

		stmt = select(RecurringTransaction).where(RecurringTransaction.next_occurrence < datetime.now())
		with Session(self.engine) as session:
			return session.scalars(stmt).all()

	def clean_up_expired_recurring_transactions(self):
		stmt = (
			select(RecurringTransaction)
			.where(RecurringTransaction.stop_after != None)
			.where(RecurringTransaction.stop_after < datetime.now())
		)
		with Session(self.engine) as session:
			for txn in session.execute(stmt):
				logging.info("Cleaning up expired recurring transaction %s" % txn)
				session.delete(txn)
			session.commit()

	def process_recurring_transaction_completion(self, id):
		with Session(self.engine) as session:
			txn = session.get(RecurringTransaction, id)
			new_occurrence = txn.next_occurrence + txn.frequency
			logging.info("Bumping next occurrence for transaction \"%s\" from %s to %s" % (txn.description, txn.next_occurrence.isoformat(), new_occurrence.isoformat()))
			txn.next_occurrence = new_occurrence
			session.commit()