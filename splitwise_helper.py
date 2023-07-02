from splitwise import Splitwise
from decimal import Decimal
from datetime import datetime, timedelta, date
import time
import re
import logging
import json

import util

logger = logging.getLogger(__name__)

class SplitwiseHelper:
	def __init__(self, creds, mint, shorthand_json_path, user_id_to_name_json_path, mint_custom_user_identifier):
		self.mint = mint
		self.mint_custom_user_identifier = mint_custom_user_identifier

		self.splitwise = Splitwise(creds['splitwise']['consumer_key'],creds['splitwise']['secret_key'],api_key=creds['splitwise']['api_key'])

		self.my_user_id = self.splitwise.getCurrentUser().getId()
		self.my_friends = self.splitwise.getFriends()
		self.user_id_to_name_overrides = {int(k):v for k,v in json.load(open(user_id_to_name_json_path)).items()} if user_id_to_name_json_path else {}
		self.shorthands_to_categories = json.load(open(shorthand_json_path))

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
			charge_modifier_used = False

			description = expense.getDescription()
			stripped_description = re.sub(r'\b[MU][A-Z]*:[A-Z]+\b', '', description).strip()
			
			my_expense_user = next(user for user in expense.getUsers() if user.getId() == self.my_user_id)
			expense_date = datetime.strptime(expense.getCreatedAt(), "%Y-%m-%dT%H:%M:%S%z")

			# first, check for shorthands
			shorthand_match = re.findall(r'\bM[A-Z]*:[A-Z]+\b', description)
			if shorthand_match and len(shorthand_match) > 1:
				logger.error("Found more than one shorthand match for Mint in a Splitwise Transaction. Skipping... Description: {}; Matches: {}".format(description, shorthand_match))
				continue

			if shorthand_match and shorthand_match[0].split(":")[1] in self.shorthands_to_categories:
				# shorthand found
				shorthand_parts = shorthand_match[0].split(":")
				category = self.shorthands_to_categories[shorthand_parts[1]]

				# Process global modifiers
				for modifier in shorthand_parts[0][1:]:
					match modifier:
						case 'C':
							# Add charge to current user's mint for the amount they paid, as well
							# Technically this could be rolled into one transaction, but having it
							# behave this way allows the software to be idempotent. If someone edits
							# an expense that has already been processed to include this flag, we
							# want to ensure that only the charge component has been added, since
							# the main component already has been added. 
							charge_modifier_used = True
							logger.info("Processing Splitwise CHARGE Transaction. Description: {}; Category: {}; Amount: {}".format(stripped_description, category, -Decimal(my_expense_user.getPaidShare())))
							self.mint.add_transaction("Splitwise: {}".format(stripped_description), -Decimal(my_expense_user.getPaidShare()), category, expense_date, "SPLIT:CHARGE{}".format(expense.getId()))
			elif shorthand_match:
				logger.error(f"Shorthand found in expense, but there is no category mapped to it! Expense: {description}")
				continue
			else:
				# otherwise look for a JSON object
				first_bracket = stripped_description.find("{")
				last_bracket = len(stripped_description) - stripped_description[::-1].find("}")
				
				if first_bracket == -1 or last_bracket == -1:
					continue
				
				json_string = stripped_description[first_bracket:last_bracket]
				desc_data = json.loads(json_string)

				if 'mint_category' not in desc_data:
					continue

				category = desc_data['mint_category']
				stripped_description = stripped_description[0:first_bracket].strip()

			# Process user-specific flags
			if self.mint_custom_user_identifier:
				user_flag_match = re.search(r'\bU{}:[A-Z]+\b'.format(self.mint_custom_user_identifier), description)
				if user_flag_match:
					user_flag = user_flag_match[0]
					modifiers = user_flag.split(":")[1]
					for modifier in modifiers:
						match modifier:
							case 'C':
								if not global_charge_modifier_used:
									charge_modifier_used = True
									logger.info("Processing Splitwise CHARGE Transaction. Description: {}; Category: {}; Amount: {}".format(stripped_description, category, -Decimal(my_expense_user.getPaidShare())))
									self.mint.add_transaction("Splitwise: {}".format(stripped_description), -Decimal(my_expense_user.getPaidShare()), category, expense_date, "SPLIT:CHARGE{}".format(expense.getId()))

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
			
			self.mint.add_transaction("Splitwise: {}".format(stripped_description), amount_owed_to_me, category, expense_date, "SPLIT:{}".format(expense.getId()), notes="\n".join(notes_array))
