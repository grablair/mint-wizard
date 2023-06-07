from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver import ChromeOptions

from decimal import Decimal

import time
from datetime import datetime, timedelta, date

import pyotp
import re
import json
import sys
import argparse

import logging

# TODO: can use better filters
# https://mint.intuit.com/transactions?categoryIds=66949270_12&startDate=2023-06-01&endDate=2023-06-30&exclHidden=T

class MintHelper:
	def __init__(self, creds, db, run_headless):
		self.creds = creds
		self.db = db

		options = ChromeOptions()

		if run_headless:
			options.add_argument("--headless")

		options.add_argument("--no-sandbox")
		options.add_argument("start-maximized")
		options.add_argument("disable-infobars")
		options.add_argument("--disable-extensions")

		self.driver = webdriver.Chrome(options)
		
		# Load the initial page
		self.load_transactions_page()

	def close(self):
		# Sleep just in case there are remaining background tasks
		time.sleep(3)

		# Close the driver
		self.driver.close()
		self.driver.quit()

	# Perform login actions. Should work for first or subsequent logins. Supports TOTP 2FA (may be required...)
	def load_transactions_page(self):
		self.driver.get("https://mint.intuit.com/transactions")

		try:
			# Try and see if the transaction table shows up
			self.wait_for_transaction_table(timeout=3)
		except TimeoutException:
			# Assume we are on the login screen, then

			try:
				account_choice = self.driver.find_element(By.CSS_SELECTOR, 'li[data-testid="AccountChoice_0"]')
				account_choice.click()
			except:
				self.get_elem_by_css('[data-testid="IdentifierFirstIdentifierInput"]') \
					.send_keys(self.creds['mint']['email'])
				self.get_elem_by_css('[data-testid="IdentifierFirstSubmitButton"]').click()

			self.get_elem_by_css('[data-testid="currentPasswordInput"]') \
				.send_keys(self.creds['mint']['password'])
			self.get_elem_by_css('[data-testid="passwordVerificationContinueButton"]').click()

			if "totp_secret" in self.creds['mint']:
				self.get_elem_by_css('[data-testid="VerifySoftTokenInput"]') \
					.send_keys(pyotp.TOTP(self.creds['mint']['totp_secret']).now())
				self.get_elem_by_css('[data-testid="VerifySoftTokenSubmitButton"]').click()

	# Waits for and returns the element by the given CSS selector
	def get_elem_by_css(self, selector, timeout=10, elem=False):
		return WebDriverWait(elem if elem else self.driver, timeout).until(
		    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
		)

	def get_elem_by_automation_id(self, id):
		return self.get_elem_by_css('[data-automation-id="{}"]'.format(id))

	def get_elems_by_description(self, desc_substring):
		return self.driver.find_elements(By.CSS_SELECTOR, 'tr[title^="Statement Name"][title*="{}"i]'.format(desc_substring))

	def get_all_transactions(self):
		self.wait_for_transaction_table()
		return self.get_elems_by_description("")

	def wait_for_transaction_table(self, hide_autoprocessed=True, timeout=60):
		self.get_elem_by_css('[data-automation-id="TRANSACTIONS_LIST_TABLE"], [class*="NoTransactionsFound"]', timeout)

		if hide_autoprocessed:
			# filter out transactions that already have AUTOPROCESSED tag
			self.search_for_transactions("-tag:AUTOPROCESSED")

	def search_for_transactions(self, query):
		time.sleep(0.25)

		self.clear_search_filters()

		search = self.get_elem_by_automation_id('TRANSACTIONS_SEARCH')
		search.clear()
		search.send_keys(query)
		search.send_keys(Keys.RETURN)

		time.sleep(0.25)

		self.wait_for_transaction_table(False)

	def clear_search_filters(self):
		try:
			while True:
				filter_chip = self.driver.find_element(By.CSS_SELECTOR, 'button[data-automation-id^="FILTER_"]:not([data-automation-id="FILTER_UNCATEGORIZED_TRANSACTIONS"])')
				filter_chip.click()
		except NoSuchElementException:
			pass

	def wait_for_edit_txn_to_close(self):
		WebDriverWait(self.driver, 10).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, '[data-automation-id="TRANSACTION_EDIT_CONTAINER"]')));

	def find_elements_by_price(self, txns, price_lambda):
		def txn_filter(txn):
			price = txn.find_element(By.CSS_SELECTOR, '[class*="StyledComponents__TransactionAmount"]').text
			return price_lambda(float(price.replace("$", "")))
		return list(filter(txn_filter, txns))

	def select_all_txns(self, txns):
		for txn in txns:
			txn.find_element(By.CSS_SELECTOR, '[class*="ChoiceItem-wrapper"]').click()
			time.sleep(2)

	def fill_category_dropdown(self, elem, category):
		current_category = elem.get_attribute("value")
		
		clear_text = ""
		for i in range(len(current_category)):
			clear_text = clear_text + Keys.BACKSPACE

		elem.send_keys(clear_text)
		elem.send_keys(category)
		self.get_elem_by_css('ul[aria-label="{}"]'.format(category)).click()
		elem.send_keys(Keys.ESCAPE)

	def recategorize_txn(self, txn, category, description=False, set_as_autoprocessed=True):
		self.get_elem_by_automation_id('EDIT_TRANSACTION_LINK').click()

		if description:
			current_description = self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').get_attribute("value")
			
			clear_text = ""
			for i in range(len(current_description)):
				clear_text = clear_text + Keys.BACKSPACE

			self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys(clear_text)
			self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys("{} ({})".format(description, current_description))


		self.fill_category_dropdown(self.get_elem_by_automation_id('ADD_TRANSACTIONS_CATEGORY'), category)

		if set_as_autoprocessed:
			self.get_elem_by_automation_id('SELECT_A_TAG').click()
			self.get_elem_by_automation_id('TAG_CHOICE_AUTOPROCESSED').click()

		actions = ActionChains(self.driver)
		actions.move_to_element(self.get_elem_by_automation_id('SAVE')).perform()

		self.get_elem_by_automation_id('SAVE').click()
		self.wait_for_edit_txn_to_close()

	def tag_txn(self, txn, tag):
		self.get_elem_by_automation_id('EDIT_TRANSACTION_LINK').click()
		self.get_elem_by_automation_id('SELECT_A_TAG').click()
		self.get_elem_by_automation_id('TAG_CHOICE_{}'.format(tag)).click()
		self.get_elem_by_automation_id('SAVE').click()
		self.wait_for_edit_txn_to_close()


	def recategorize_all_txns(self, txns, category, set_as_autoprocessed=True):
		for txn in txns:
			self.recategorize_txn(txn, category, set_as_autoprocessed)

	def add_transaction(self, desc, price, category, date, dedupe, notes=False):
		price = Decimal(price)

		if price == 0:
			return

		# TODO: Add local dedupe caching to avoid unnecessary searches
		self.search_for_transactions(dedupe)
		if self.get_elems_by_description(dedupe):
			logging.info("Duplicate found: %s (dedupe string: %s). Skipping..." % (desc, dedupe))
			return

		logging.info("Adding transaction for %s with price %s and category %s" % (desc, price, category))

		self.clear_search_filters()

		self.get_elem_by_automation_id('PLUS_ADD_TRANSACTION').click()
		self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys("{} | {}".format(desc, dedupe))

		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="month-field"]').send_keys(date.month)
		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="day-field"]').send_keys(date.day)
		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="year-field"]').send_keys(date.year)

		self.fill_category_dropdown(self.get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_CATEGORY"]'), category)

		self.get_elem_by_automation_id('ADD_TRANSACTIONS_AMOUNT').clear()
		self.get_elem_by_automation_id('ADD_TRANSACTIONS_AMOUNT').send_keys(str(price.copy_abs()))	

		if price.compare(0) == 1:
			# click the "Income" button
			self.driver.find_element(By.XPATH, "/html/body//div[@data-automation-id='TRANSACTION_EDIT_CONTAINER']//fieldset[contains(@class, 'ChoiceGroup')]//label").click()

		if notes:
			self.get_elem_by_automation_id("ADD_NOTE").send_keys(notes)

		self.get_elem_by_automation_id('SAVE').click()

		self.wait_for_edit_txn_to_close()

	def recategorize_target_transactions(self, pattern_configs):
		logging.info("Starting to recategorize target transactions by pattern")

		for pattern, category, new_description in pattern_configs:
			logging.info("Processing pattern %s; recategorizing matches to %s with new description %s" % (pattern, category, new_description))

			txns = self.get_all_transactions()
			txns.reverse()

			for txn in txns:
				statement_name = txn.get_attribute("title")[len("Statement Name: "):]
				if "AUTOCATEGORIZED" in statement_name:
					logging.info("Skipping matching transaction %s as it's already auto-categorized" % statement_name.replace(" | AUTOCATEGORIZED", "").strip())
					continue

				if re.match(patterns_to_hide, statement_name):
					logging.info("Recategorizing matching transaction %s to %s, as it matches pattern %s" % (statement_name, category, pattern))
					self.recategorize_txn(txn, category, description="%s | AUTOCATEGORIZED" % new_description)

	def process_recurring_transactions(self):
		logging.info("Processing recurring transactions")

		for txn in self.db.get_past_due_recurring_transactions():
			# sanity check
			if txn.next_occurrence < datetime.now():
				logging.info("Creating transaction for %s" % txn)
				self.add_transaction(txn.description, txn.amount, txn.category, txn.next_occurrence, "RECUR:%s:%s" % (txn.dedupe_string, txn.next_occurrence.isoformat()))
				self.db.process_recurring_transaction_completion(txn.id)