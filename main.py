from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from splitwise import Splitwise

from math import isclose

from decimal import Decimal

import time
from datetime import datetime, timedelta

import pyotp
import re
import json
import sys

cred_file = open(sys.argv[1])
creds = json.load(cred_file)

chromedriver_path = sys.argv[2]

HIDE_CATEGORY = "Hide from Budgets & Trends"
NEEDS_ATTENTION_TAG = "NEEDS_ATTENTION"

MY_SPLITWISE_USER_ID = 1111214
SPLITWISE_USER_ID_TO_NAME = {
	29972811: "Sebastian Hulburt",
	33394288: "Aaron Parisi",
	27378300: "Jakeb Blair",
	11216475: "Kevin Kelley",
	33394114: "Sally Blair",
	33000074: "Courtney Blair",
	24032731: "Valerie Reid",
	1111214:  "Graham Blair"
}

SPLITWISE_SHORTHANDS_TO_CATEGORIES = {
	"M:TV": "Television",
	"M:R": "Restaurants",
	"M:FF": "Fast Food",
	"M:BAR": "Alcohol & Bars",
	"M:COFFEE": "Coffee Shops",
	"M:GROC": "Groceries",
	"M:UTIL": "Utilities",
	"M:HEAT": "Heating",
	"M:RID": "Ridwell",
	"M:INTERNET": "Internet",
	"M:SHOP": "Shopping",
	"M:HT": "Hot Tub",
	"M:AIR": "Air Travel",
	"M:VAC": "Vacation",
	"M:RENT": "Mortgage & Rent",
	"M:COHAB": "Mortgage & Rent",
	"M:PHONE": "Mobile Phone"
}

# Start driver first and Splitwise first
driver = webdriver.Chrome(executable_path=chromedriver_path)
splitwise = Splitwise(creds['splitwise']['consumer_key'],creds['splitwise']['secret_key'],api_key=creds['splitwise']['api_key'])

def get_elem_by_css(selector, timeout=10, elem=driver):
	return WebDriverWait(elem, timeout).until(
	    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
	)

def get_elem_by_automation_id(id):
	return get_elem_by_css('[data-automation-id="{}"]'.format(id))

def login():
	try:
		account_choice = driver.find_element(By.CSS_SELECTOR, 'li[data-testid="AccountChoice_0"]')
		account_choice.click()
	except:
		get_elem_by_css('[data-testid="IdentifierFirstIdentifierInput"]') \
			.send_keys(creds['mint']['email'])
		get_elem_by_css('[data-testid="IdentifierFirstSubmitButton"]').click()

	get_elem_by_css('[data-testid="currentPasswordInput"]') \
		.send_keys(creds['mint']['password'])
	get_elem_by_css('[data-testid="passwordVerificationContinueButton"]').click()

	if "totp_secret" in creds['mint']:
		get_elem_by_css('[data-testid="VerifySoftTokenInput"]') \
			.send_keys(pyotp.TOTP(creds['mint']['totp_secret']).now())
		get_elem_by_css('[data-testid="VerifySoftTokenSubmitButton"]').click()

def get_elems_by_description(desc_substring):
	return driver.find_elements(By.CSS_SELECTOR, 'tr[title^="Statement Name"][title*="{}"i]'.format(desc_substring))

def wait_for_transaction_table(hide_autoprocessed=True):
	get_elem_by_css('[data-automation-id="TRANSACTIONS_LIST_TABLE"], [class*="NoTransactionsFound"]', 60)

	if hide_autoprocessed:
		# filter out transactions that already have AUTOPROCESSED tag
		search_for_transactions("-tag:AUTOPROCESSED")

def search_for_transactions(query):
	clear_search_filters()

	search = get_elem_by_css('input[data-automation-id="TRANSACTIONS_SEARCH"]')
	search.clear()
	search.send_keys(query)
	search.send_keys(Keys.RETURN)

	wait_for_transaction_table(False)

def clear_search_filters():
	try:
		while True:
			filter_chip = driver.find_element(By.CSS_SELECTOR, 'button[data-automation-id^="FILTER_"]:not([data-automation-id="FILTER_UNCATEGORIZED_TRANSACTIONS"])')
			filter_chip.click()
	except NoSuchElementException:
		pass

def wait_for_edit_txn_to_close():
	WebDriverWait(driver, 10).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, '[data-automation-id="TRANSACTION_EDIT_CONTAINER"]')));

def find_elements_by_price(txns, price_lambda):
	def txn_filter(txn):
		price = txn.find_element(By.CSS_SELECTOR, '[class*="StyledComponents__TransactionAmount"]').text
		return price_lambda(float(price.replace("$", "")))
	return list(filter(txn_filter, txns))

def select_all_txns(txns):
	for txn in txns:
		txn.find_element(By.CSS_SELECTOR, '[class*="ChoiceItem-wrapper"]').click()
		time.sleep(2)

def fill_category_dropdown(elem, category):
	current_category = elem.get_attribute("value")
	
	clear_text = ""
	for i in range(len(current_category)):
		clear_text = clear_text + Keys.BACKSPACE

	elem.send_keys(clear_text)
	elem.send_keys(category)
	get_elem_by_css('ul[aria-label="{}"]'.format(category)).click()
	elem.send_keys(Keys.ESCAPE)

def recategorize_txn(txn, category, description=False, set_as_autoprocessed=True):
	get_elem_by_css('button[data-automation-id="EDIT_TRANSACTION_LINK"]', elem=txn).click()

	if description:
		current_description = get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_DESCRIPTION"]').get_attribute("value")
		
		clear_text = ""
		for i in range(len(current_description)):
			clear_text = clear_text + Keys.BACKSPACE

		get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_DESCRIPTION"]').send_keys(clear_text)
		get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_DESCRIPTION"]').send_keys("{} ({})".format(description, current_description))


	fill_category_dropdown(get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_CATEGORY"]'), category)

	if set_as_autoprocessed:
		get_elem_by_css('input[data-automation-id="SELECT_A_TAG"]').click()
		get_elem_by_css('ul[data-automation-id="TAG_CHOICE_AUTOPROCESSED"]').click()

	get_elem_by_css('button[data-automation-id="SAVE"]').click()
	wait_for_edit_txn_to_close()

def tag_txn(txn, tag):
	get_elem_by_css('button[data-automation-id="EDIT_TRANSACTION_LINK"]', elem=txn).click()
	get_elem_by_css('input[data-automation-id="SELECT_A_TAG"]').click()
	get_elem_by_css('ul[data-automation-id="TAG_CHOICE_{}"]'.format(tag)).click()
	get_elem_by_css('button[data-automation-id="SAVE"]').click()
	wait_for_edit_txn_to_close()


def recategorize_all_txns(txns, category, set_as_autoprocessed=True):
	for txn in txns:
		recategorize_txn(txn, category, set_as_autoprocessed)

def add_transaction(desc, price, category, date, dedupe, notes=False):
	price = Decimal(price)

	if price == 0:
		return

	search_for_transactions(dedupe)
	if get_elems_by_description(dedupe):
		print("Duplicate found: {} (dedupe string: {}). Skipping...".format(desc, dedupe))
		return

	clear_search_filters()

	get_elem_by_css('button[data-automation-id="PLUS_ADD_TRANSACTION"]').click()
	get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_DESCRIPTION"]').send_keys("{} | {}".format(desc, dedupe))

	get_elem_by_css('span[data-date-field="true"][aria-labelledby*="month-field"]').send_keys(date.month)
	get_elem_by_css('span[data-date-field="true"][aria-labelledby*="day-field"]').send_keys(date.day)
	get_elem_by_css('span[data-date-field="true"][aria-labelledby*="year-field"]').send_keys(date.year)

	fill_category_dropdown(get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_CATEGORY"]'), category)

	get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_AMOUNT"]').clear()
	get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_AMOUNT"]').send_keys(str(price.copy_abs()))	

	if price.compare(0) == 1:
		# click the "Income" button
		driver.find_element(By.XPATH, "/html/body//div[@data-automation-id='TRANSACTION_EDIT_CONTAINER']//fieldset[contains(@class, 'ChoiceGroup')]//label").click()

	if notes:
		get_elem_by_automation_id("ADD_NOTE").send_keys(notes)

	get_elem_by_css('button[data-automation-id="SAVE"]').click()

	wait_for_edit_txn_to_close()

def money_str_to_decimal(money_str):
	return Decimal(re.sub(r'[\$,]', '', money_str))

def handle_apple_monthly_installments():
	""" Hides / recategorizes auto-recurring Apple Card monthly installments """
	instances_to_recategorize = [
		(-17.87, "Apple Watch Payment", "Mobile Phone"),
		(-22.87, "iPhone 13 Pro Payment", "Mobile Phone"), 
		(-32.87, "iPhone 14 Pro Payment", "Mobile Phone"),
		(-45.79, "Courtney's Phone", HIDE_CATEGORY),
		(-11.20, "Courtney's Insurance", HIDE_CATEGORY),
		(-45.79, "Kevin's Phone", HIDE_CATEGORY),
		(-11.20, "Kevin's Insurance", HIDE_CATEGORY)
	]

	txns = get_elems_by_description("Monthly Installments")
	txns.reverse()

	# TODO: group these by date, make properly idempotent

	for txn in txns:
		raw_price = txn.find_element(By.CSS_SELECTOR, '[class*="StyledComponents__TransactionAmount"]').text
		price = float(raw_price.replace("$", "")) 

		for instance_price, desc, category in list(instances_to_recategorize):
			if isclose(price, instance_price):
				instances_to_recategorize.remove((instance_price, desc, category))
				recategorize_txn(txn, category, description=desc)
				break
		else:
			tag_txn(txn, NEEDS_ATTENTION_TAG)


def handle_paycheck():
	txns = get_elems_by_description("External Deposit - AMAZON DEV")

	for txn in txns:
		recategorize_txn(txn, "Paycheck", description="Amazon Paycheck")

def handle_splitwise_expenses():
	expenses = splitwise.getExpenses(dated_after=(datetime.now() - timedelta(days=7)), dated_before=datetime.now(), limit=200)

	for expense in expenses:
		description = expense.getDescription()

		# first, check for shorthands
		shorthand_match = re.search(r'\bM:[A-Z]+\b', description)
		if shorthand_match and shorthand_match[0] in SPLITWISE_SHORTHANDS_TO_CATEGORIES:
			# shorthand found
			category = SPLITWISE_SHORTHANDS_TO_CATEGORIES[shorthand_match[0]]
			desc_without_json = re.sub(r'\bM:[A-Z]+\b', '', description).strip()
		else:
			# otherwise look for a JSON object
			first_bracket = description.find("{")
			last_bracket = len(description) - description[::-1].find("}")
			
			if first_bracket == -1 or last_bracket == -1:
				continue
			
			desc_without_json = description[0:first_bracket].strip()
			json_string = description[first_bracket:last_bracket]
			desc_data = json.loads(json_string)

			if 'mint_category' not in desc_data:
				continue

			category = desc_data['mint_category']

		# For some charges like payment plans, we want to add the entire charge
		if 'charge_full_amount_also' in desc_data and desc_data['charge_full_amount_also']:
			add_transaction("Splitwise: {}".format(desc_without_json), -Decimal(expense.getCost()), category, expense_date, "SPLIT:CHARGE{}".format(expense.getId()))

		amount_owed_to_me = Decimal(0)
		notes_array = []
		for debt in expense.getRepayments():
			amount = money_str_to_decimal(debt.getAmount())
			if debt.getToUser() == MY_SPLITWISE_USER_ID:
				amount_owed_to_me += amount
				notes_array.append("{} -> Me: {}".format(SPLITWISE_USER_ID_TO_NAME[debt.getFromUser()], amount))
			elif debt.getFromUser() == MY_SPLITWISE_USER_ID:
				amount_owed_to_me -= amount
				notes_array.append("Me -> {}: {}".format(SPLITWISE_USER_ID_TO_NAME[debt.getToUser()], amount))

		if amount_owed_to_me == Decimal(0):
			continue

		print("Processing Splitwise Transaction:\n\tDescription: {}\n\tCategory: {}\n\tAmount: {}\n\tDebts:\n\t\t{}".format(desc_without_json, category, amount_owed_to_me, "\n\t\t".join(notes_array)))
		
		expense_date = datetime.fromisoformat(expense.getCreatedAt())
		add_transaction("Splitwise: {}".format(desc_without_json), amount_owed_to_me, category, expense_date, "SPLIT:{}".format(expense.getId()), notes="\n".join(notes_array))


# Log in
driver.get("https://mint.intuit.com/transactions")
login()

# Wait for table to appear
wait_for_transaction_table()

# Handle all cases
handle_paycheck()
handle_splitwise_expenses()
handle_apple_monthly_installments()

time.sleep(5)

#close the driver
driver.close()
driver.quit()

print("Mint auto-processing complete!")