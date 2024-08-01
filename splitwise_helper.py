from splitwise import Splitwise
from splitwise.expense import Expense
from splitwise.user import ExpenseUser
from decimal import Decimal
from datetime import datetime, timedelta, date
import time
import re
import logging
import json

import util

logger = logging.getLogger(__name__)

class SplitwiseHelper:
	def __init__(self, creds, budgeting_app, shorthand_json_path, user_id_to_name_json_path, custom_user_identifier, db):
		self.budgeting_app = budgeting_app
		self.custom_user_identifier = custom_user_identifier

		self.splitwise = Splitwise(creds['splitwise']['consumer_key'],creds['splitwise']['secret_key'],api_key=creds['splitwise']['api_key'])

		self.my_user_id = self.splitwise.getCurrentUser().getId()
		self.my_friends = self.splitwise.getFriends()
		self.user_id_to_name_overrides = {int(k):v for k,v in json.load(open(user_id_to_name_json_path)).items()} if user_id_to_name_json_path else {}
		self.shorthands_to_categories = json.load(open(shorthand_json_path))
		self.db = db

	# Translates a Splitwise user ID to a name
	def splitwise_user_id_to_name(self, user_id):
		if user_id in self.user_id_to_name_overrides:
			return self.user_id_to_name_overrides[user_id]

		if user_id == self.my_user_id:
			me = self.splitwise.getCurrentUser()
			return "{} {}".format(me.getFirstName(), me.getLastName()).strip()

		for friend in self.my_friends:
			if friend.getId() == user_id:
				return "{} {}".format(friend.getFirstName(), friend.getLastName()).strip()

	def process_splitwise_expenses(self, days_to_look_back):
		logger.info("Starting to process Splitwise expenses, looking back %s days" % days_to_look_back)

		expenses = self.splitwise.getExpenses(updated_after=(datetime.now() - timedelta(days=days_to_look_back)), updated_before=datetime.now(), limit=200)

		logger.info("%s expenses to process" % len(expenses))
		for expense in expenses:
			# skip if the transaction is deleted
			if expense.getDeletedAt():
				continue

			process_txn_func = self.budgeting_app.add_transaction

			charge_modifier_used = False

			description = expense.getDescription()
			stripped_description = re.sub(r'\b[MUD][A-Z]*:[A-Z0-9]+\b', '', description).strip()
			
			try:
				my_expense_user = next(user for user in expense.getUsers() if user.getId() == self.my_user_id)
			except StopIteration:
				# must be a group expense I am not part of
				continue

			expense_date = datetime.strptime(expense.getDate(), "%Y-%m-%dT%H:%M:%S%z")

			# first, check for shorthands
			shorthand_match = re.findall(r'\bM[A-Z]*:[A-Z]+\b', description)
			if shorthand_match and len(shorthand_match) > 1:
				logger.error("Found more than one catergory shorthand match in a Splitwise Transaction. Skipping... Description: {}; Matches: {}".format(description, shorthand_match))
				continue

			# now, let's see if the delay tag is also present
			delay_match = re.findall(r'\bD[A-Z]*:[0-9]+\b'.format(self.custom_user_identifier), description)
			if delay_match:
				# The "Delay" modifier has been used for this transaction. Let's extract the
				# number of days to delay from the delay tag
				if len(delay_match) > 1:
					logger.error("Found more than one section for the transaction delay tag. Skipping... Description: {}; Matches: {}".format(description, delay_match))
					continue

				# extract tag; check if the tag is for the current user (or every user)
				tag = delay_match[0].split(":")[0]
				if len(tag) == 1 or tag[1:] == self.custom_user_identifier:
					# extract days
					delay_days = int(delay_match[0].split(":")[1])
					expense_date += timedelta(days=delay_days)

					# override the transaction processing function
					process_txn_func = self.db.schedule_single_transaction

					logger.info(f"Delay modifier found for transaction. Description: {stripped_description}; Days: {delay_days}; New Date: {expense_date}")

			if shorthand_match and shorthand_match[0].split(":")[1] in self.shorthands_to_categories:
				# shorthand found
				shorthand_parts = shorthand_match[0].split(":")
				category = self.shorthands_to_categories[shorthand_parts[1]]

				# Process global modifiers
				for modifier in shorthand_parts[0][1:]:
					match modifier:
						case 'C':
							# Add charge to current user's budgeting app for the amount they paid, as
							# well. Technically this could be rolled into one transaction, but having it
							# behave this way allows the software to be idempotent. If someone edits
							# an expense that has already been processed to include this flag, we
							# want to ensure that only the charge component has been added, since
							# the main component already has been added.
							charge_modifier_used = True
							logger.info("Processing Splitwise CHARGE Transaction. Description: {}; Category: {}; Amount: {}".format(stripped_description, category, -Decimal(my_expense_user.getPaidShare())))

							txn_desc = "SW: {}".format(stripped_description)
							dedupe = "SPLIT:CHARGE{}".format(expense.getId())
							amount = -Decimal(my_expense_user.getPaidShare())

							process_txn_func(txn_desc, amount, category, expense_date, dedupe)
			else:
				if shorthand_match:
					logger.error(f"Shorthand found in expense, but there is no category mapped to it! Expense: {description}")
				continue

			# Process user-specific flags
			if self.custom_user_identifier:
				user_flag_match = re.search(r'\bU{}:[A-Z]+\b'.format(self.custom_user_identifier), description)
				if user_flag_match:
					user_flag = user_flag_match[0]
					modifiers = user_flag.split(":")[1]
					for modifier in modifiers:
						match modifier:
							case 'C':
								if not charge_modifier_used:
									charge_modifier_used = True
									logger.info("Processing Splitwise CHARGE Transaction. Description: {}; Category: {}; Amount: {}".format(stripped_description, category, -Decimal(my_expense_user.getPaidShare())))

									txn_desc = "SW: {}".format(stripped_description)
									dedupe = "SPLIT:CHARGE{}".format(expense.getId())
									amount = -Decimal(my_expense_user.getPaidShare())

									process_txn_func(txn_desc, amount, category, expense_date, dedupe)

			amount_owed_to_me = Decimal(my_expense_user.getPaidShare()) - Decimal(my_expense_user.getOwedShare())
			if amount_owed_to_me == 0:
				continue

			notes_array = []
			for debt in expense.getRepayments():
				amount = util.money_str_to_decimal(debt.getAmount())
				if debt.getToUser() == self.my_user_id:
					notes_array.append("{} -> Me: {}".format(self.splitwise_user_id_to_name(debt.getFromUser()), amount))
				elif debt.getFromUser() == self.my_user_id:
					notes_array.append("Me -> {}: {}".format(self.splitwise_user_id_to_name(debt.getToUser()), amount))


			logger.info("Processing Splitwise Transaction. Description: {}; Category: {}; Amount: {}; {}Debts:{}".format(
				stripped_description, 
				category, 
				amount_owed_to_me, 
				"Extra Charge Transaction Needed: {}; ".format(-Decimal(my_expense_user.getPaidShare())) if charge_modifier_used else "", 
				notes_array))
			
			process_txn_func(
				"SW: {}".format(stripped_description),
				amount_owed_to_me,
				category,
				expense_date,
				"SPLIT:{}".format(expense.getId()),
				notes="\n".join(notes_array))

	def import_payments_from_budgeting_app(self, rules):
		logger.info("Processing payments from budgeting app")

		for rule in rules:
			user_id = rule['user_id']
			txns = list(filter(lambda txn: all([search not in txn['merchant']['name'] and search not in str(txn['notes']) for search in ["LOANPAYMENT", "LOANINTERESTPAYMENT"]]),
				self.budgeting_app.search_transactions(search=rule['search_string'], limit=5)))

			logger.info(f"Processing following payments: {txns}")

			for txn in txns:
				expenses = list(filter(lambda e: not e.getDeletedAt(), self.splitwise.getExpenses(
					friend_id    = user_id,
					dated_after  = (date.fromisoformat(txn['date']) - timedelta(days=1)).isoformat(),
					dated_before = (date.fromisoformat(txn['date']) + timedelta(days=1)).isoformat())))

				if not any(f"B:{txn['id']}" in e.getDescription() for e in expenses):
					expense = Expense()
					expense.setCost(txn['amount'])
					expense.setDescription(f"Direct Payment B:{txn['id']}")
					expense.setDate(txn['date'])

					me = ExpenseUser()
					me.setId(self.my_user_id)
					me.setPaidShare('0.00')
					me.setOwedShare(txn['amount'])

					other = ExpenseUser()
					other.setId(user_id)
					other.setPaidShare(txn['amount'])
					other.setOwedShare('0.00')

					users = []
					users.append(me)
					users.append(other)

					expense.setUsers(users)

					logger.info(f"Creating Splitwise payment expense of ${txn['amount']} from {self.splitwise_user_id_to_name(user_id)} ({user_id}) on date {txn['date']}")

					expense, errors = self.splitwise.createExpense(expense)

					if errors:
						logger.error(f"Error while creating payment expense: {errors}")
					else:
						logger.info(f"Payment expense created")

	# processes personal loan interest charges, based on provided rates in the config
	def handle_personal_loans(self, loans):
		logger.info("Processing personal loan interest")
		today = date.today()
		start_of_prior_month = (today - timedelta(days=today.day)).replace(day=1)
		start_of_this_month = today.replace(day=1)
		last_day_of_prior_month = start_of_this_month - timedelta(days=1)

		for loan in loans:
			user_id               = loan['user_id']
			rate                  = loan['rate']
			budget_category       = loan['budget_category']
			interest_free_balance = loan.get('interest_free_balance', 0)

			logger.info(f"Processing loan for user {self.splitwise_user_id_to_name(user_id)} ({user_id}), with rate of {rate}")

			friend = next(f for f in self.my_friends if f.getId() == user_id)

			expenses = list(filter(lambda e: not e.getDeletedAt(), self.splitwise.getExpenses(
				friend_id    = user_id,
				dated_after  = start_of_prior_month.isoformat(),
				dated_before = start_of_this_month.isoformat())))

			# New charges to other user
			expenses_i_paid_for = list(filter(lambda e: float(next(u for u in e.getUsers() if u.getId() == self.my_user_id).getPaidShare()) > 0 and not "LOANSTART" in e.getDescription(), expenses))
			new_charges = sum(float(next(eu.getOwedShare() for eu in e.getUsers() if eu.getId() == user_id)) for e in expenses_i_paid_for)

			# Interest to charge
			current_total = max(0, sum(float(b.getAmount()) for b in friend.getBalances()) - interest_free_balance)
			balance_to_accrue = current_total - new_charges
			interest = max(0, balance_to_accrue * (rate / 12))

			logger.info(f"    Current total: ${current_total}")
			logger.info(f"    Balance to accrue: ${balance_to_accrue}")
			logger.info(f"    Interest: ${interest}")

			if not any("Personal Loan Interest" in e.getDescription() or "LOANSTART" in e.getDescription() for e in expenses):
				if interest > 0:
					expense = Expense()
					expense.setCost(interest)
					expense.setDescription("Personal Loan Interest")
					expense.setDate(last_day_of_prior_month.isoformat())

					me = ExpenseUser()
					me.setId(self.my_user_id)
					me.setPaidShare(interest)
					me.setOwedShare('0.00')

					other = ExpenseUser()
					other.setId(user_id)
					other.setPaidShare('0.00')
					other.setOwedShare(interest)

					users = []
					users.append(me)
					users.append(other)

					expense.setUsers(users)

					logger.info(f"Creating interest expense on Splitwise of ${interest} to {self.splitwise_user_id_to_name(user_id)} ({user_id}) on date {last_day_of_prior_month.isoformat()}")

					expense, errors = self.splitwise.createExpense(expense)

					if errors:
						logger.error(f"Error while creating interest expense: {errors}")
					else:
						logger.info(f"Interest expense created")

			# Money coming to me
			expenses_they_paid_for = list(filter(lambda e: float(next(u for u in e.getUsers() if u.getId() == user_id).getPaidShare()) > 0, expenses))
			new_payments = sum(float(next(eu.getOwedShare() for eu in e.getUsers() if eu.getId() == self.my_user_id)) for e in expenses_they_paid_for)

			if new_payments > 0 and interest > 0:
				logger.info(f"Creating loan interest paid transaction on Monarch of ${min(interest, new_payments)} in category \"Interest\" for month {last_day_of_prior_month.isoformat()}")

				self.budgeting_app.add_transaction(
					f"Personal Loan Interest Paid from {self.splitwise_user_id_to_name(user_id)}",
					min(interest, new_payments),
					"Interest",
					today,
					f"LOANINTERESTPAYMENT:{last_day_of_prior_month.isoformat()}",
					notes=f"New Charges: {new_charges}\nNew Payments: {new_payments}\nInterest: {interest}")

			if new_charges - new_payments < 0:
				logger.info(f"Creating loan payment transaction on Monarch of ${-1 * (new_charges - new_payments)} in category \"{budget_category}\" for month {last_day_of_prior_month.isoformat()}")

				self.budgeting_app.add_transaction(
					f"Personal Loan Principal Payment from {self.splitwise_user_id_to_name(user_id)}",
					-1 * (new_charges - new_payments),
					budget_category,
					today,
					f"LOANPAYMENT:{last_day_of_prior_month.isoformat()}",
					notes=f"New Charges: {new_charges}\nNew Payments: {new_payments}\nInterest: {interest}")
