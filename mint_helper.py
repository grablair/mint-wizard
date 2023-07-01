from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver import ChromeOptions, FirefoxOptions
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

from decimal import Decimal

import time
from datetime import datetime, timedelta, date

import pyotp
import re
import json
import sys
import argparse

import logging

from db import get_next_occurrence_for_txn


logger = logging.getLogger(__name__)

# TODO: can use better filters
# e.g. https://mint.intuit.com/transactions?categoryIds=66949270_12&startDate=2023-06-01&endDate=2023-06-30&exclHidden=T

class MintHelper:
	def __init__(self, creds, db, run_headless, driver_type, run_remote, remote_url):
		self.creds = creds
		self.db = db
		self.driver_type = driver_type
		self.run_remote = run_remote
		self.remote_url = remote_url

		if driver_type == "FIREFOX":
			self.options = FirefoxOptions()
			self.desired_capabilities = DesiredCapabilities.FIREFOX
		elif driver_type == "CHROME":
			self.options = ChromeOptions()
			self.desired_capabilities = DesiredCapabilities.CHROME
		else:
			raise ValueError(f"Only support Chrome and Firefox drivers right now. Specified: {driver_type}")
		
		if run_headless:
			self.options.add_argument("--headless")

		self.options.add_argument("--no-sandbox")
		self.options.add_argument("window-size=1900x1700")
		self.options.add_argument("--enable-javascript")

	def __enter__(self):
		if self.run_remote:
			self.driver = webdriver.Remote(
				command_executor=self.remote_url, 
				options=self.options, 
				desired_capabilities=self.desired_capabilities)
		if self.driver_type == "FIREFOX":
			self.driver = webdriver.Firefox(options=self.options)
		elif self.driver_type == "CHROME":
			self.driver = webdriver.Chrome(options=self.options)
		else:
			raise ValueError(f"Only support Chrome and Firefox drivers right now. Specified: {driver_type}")

		self.driver.set_window_size(1900, 1700)

		return self

	def __exit__(self, exc_type, exc_value, traceback):
		logger.info("Closing session")

		self.driver.quit()

		time.sleep(5)

	def close(self):
		# Sleep just in case there are remaining background tasks
		time.sleep(3)

		# Close the driver
		self.driver.quit()

	# Perform login actions. Should work for first or subsequent logins. Supports TOTP 2FA (may be required...)
	def load_transactions_page(self):
		self.driver.get("https://mint.intuit.com/transactions")

		try:
			# Try and see if the transaction table shows up
			self.wait_for_transaction_table(timeout=10)
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

	def center_elem(self, elem):
		if elem:
			self.driver.execute_script('arguments[0].scrollIntoView({block: "center"});', elem)

	# Waits for and returns the element by the given CSS selector
	def get_elem_by_css(self, selector, timeout=10, elem=False):
		self.hide_account_status_bar()
		elem = WebDriverWait(elem if elem else self.driver, timeout).until(
		    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
		)
		self.center_elem(elem)
		return elem


	def get_elem_by_automation_id(self, id, elem=False):
		return self.get_elem_by_css('[data-automation-id="{}"]'.format(id), elem=elem)

	def get_elems_by_description(self, desc_substring):
		self.hide_account_status_bar()
		self.wait_for_transaction_table()
		return self.driver.find_elements(By.CSS_SELECTOR, 'tr[title^="Statement Name"][title*="{}"i]'.format(desc_substring))

	def get_all_transactions(self):
		self.hide_account_status_bar()
		self.wait_for_transaction_table()
		return self.driver.find_elements(By.CSS_SELECTOR, 'tr[title^="Statement Name"]')

	def hide_account_status_bar(self):
		self.driver.execute_script("let result = document.querySelector('div[class*=\"AccountStatusBar\"]'); if (result != null) result.style.display = 'none';")

	def wait_for_transaction_table(self, hide_autoprocessed=True, timeout=60):
		self.get_elem_by_css('[data-automation-id="TRANSACTIONS_LIST_TABLE"], [class*="NoTransactionsFound"]', timeout)
		self.hide_account_status_bar()

		if hide_autoprocessed:
			# filter out transactions that already have AUTOPROCESSED tag
			self.search_for_transactions("-tag:AUTOPROCESSED")

	def search_for_transactions(self, query):
		self.hide_account_status_bar()

		time.sleep(0.25)

		self.clear_search_filters()

		search = self.get_elem_by_automation_id('TRANSACTIONS_SEARCH')
		search.clear()
		search.send_keys(query)
		search.send_keys(Keys.RETURN)

		time.sleep(0.25)

		self.wait_for_transaction_table(False)

	def clear_search_filters(self):
		self.hide_account_status_bar()

		try:
			while True:
				filter_chip = self.driver.find_element(By.CSS_SELECTOR, 'button[data-automation-id^="FILTER_"]:not([data-automation-id="FILTER_UNCATEGORIZED_TRANSACTIONS"])')
				self.center_elem(filter_chip)
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
			self.get_elem_by_css('[class*="ChoiceItem-wrapper"]').click()
			time.sleep(2)

	def fill_category_dropdown(self, category):
		elem = self.get_elem_by_css('input[data-automation-id="ADD_TRANSACTIONS_CATEGORY"]')

		current_category = elem.get_attribute("value")
		
		clear_text = ""
		for i in range(len(current_category)):
			clear_text = clear_text + Keys.BACKSPACE

		elem.send_keys(clear_text)
		elem.send_keys(category)
		self.get_elem_by_css('ul[aria-label="{}"]'.format(category)).click()
		elem.send_keys(Keys.ESCAPE)

	def recategorize_txn(self, txn, category, description=False, set_as_autoprocessed=True):
		self.hide_account_status_bar()
		self.get_elem_by_automation_id('EDIT_TRANSACTION_LINK', elem=txn).click()

		if description:
			current_description = self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').get_attribute("value")
			
			clear_text = ""
			for i in range(len(current_description)):
				clear_text = clear_text + Keys.BACKSPACE

			self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys(clear_text)
			self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys("{} ({})".format(description, current_description))


		self.fill_category_dropdown(category)

		if set_as_autoprocessed:
			self.get_elem_by_automation_id('SELECT_A_TAG').click()
			self.get_elem_by_automation_id('TAG_CHOICE_AUTOPROCESSED').click()

		self.driver.execute_script('arguments[0].scrollIntoView({block: "center"});', self.get_elem_by_automation_id('SAVE'))

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
			logger.info("Duplicate found: %s (dedupe string: %s). Skipping..." % (desc, dedupe))
			return

		logger.info("Adding transaction for \"%s\" with price $%s and category \"%s\"" % (desc, price, category))

		self.clear_search_filters()

		self.get_elem_by_automation_id('PLUS_ADD_TRANSACTION').click()
		self.get_elem_by_automation_id('ADD_TRANSACTIONS_DESCRIPTION').send_keys("{} | {}".format(desc, dedupe))

		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="month-field"]').send_keys(date.month)
		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="day-field"]').send_keys(date.day)
		self.get_elem_by_css('span[data-date-field="true"][aria-labelledby*="year-field"]').send_keys(date.year)

		self.fill_category_dropdown(category)

		self.get_elem_by_automation_id('ADD_TRANSACTIONS_AMOUNT').clear()
		self.get_elem_by_automation_id('ADD_TRANSACTIONS_AMOUNT').send_keys(str(price.copy_abs()))	

		if price.compare(0) == 1:
			# click the "Income" button
			self.driver.find_element(By.XPATH, "/html/body//div[@data-automation-id='TRANSACTION_EDIT_CONTAINER']//fieldset[contains(@class, 'ChoiceGroup')]//label").click()

		if notes:
			self.get_elem_by_automation_id("ADD_NOTE").send_keys(notes)

		self.get_elem_by_automation_id('SAVE').click()

		logger.info("Added transaction for \"%s\" with price $%s and category \"%s\"" % (desc, price, category))

		self.wait_for_edit_txn_to_close()

	def get_txn_statement_name(self, txn):
		return txn.get_attribute("title")[len("Statement Name: "):]

	def recategorize_target_transactions(self, pattern_configs):
		logger.info("Starting to recategorize target transactions by pattern")

		self.wait_for_transaction_table()

		for pattern, category, new_description in pattern_configs:
			logger.info("Processing pattern /%s/; recategorizing matches to \"%s\" with new description \"%s\"" % (pattern, category, new_description))

			txns = self.get_all_transactions()
			txns.reverse()

			txns = list(filter(lambda txn: re.search(pattern, self.get_txn_statement_name(txn)), txns))

			logger.info("%s matches found for pattern" % len(txns))
			for txn in txns:
				statement_name = self.get_txn_statement_name(txn)
				if "AUTOCATEGORIZED" in statement_name:
					logger.info("Skipping matching transaction \"%s\" as it's already auto-categorized" % statement_name.replace(" | AUTOCATEGORIZED", "").strip())
					continue

				logger.info("Renaming matching transaction \"%s\" to \"%s\", and recategorizing as \"%s\"" % (statement_name, new_description, category))
				self.recategorize_txn(txn, category, description="%s | AUTOCATEGORIZED" % new_description)
				logger.info("Transaction recategorized")

	def process_recurring_transactions(self):
		logger.info("Processing recurring transactions")

		txns = self.db.get_past_due_recurring_transactions()
		logger.info("%s recurring transactions to process in first iteration" % len(txns))
		while txns:
			for txn in txns:
				logger.info("Creating transaction for \"%s\"" % txn)

				next_occurrence = get_next_occurrence_for_txn(txn)
				self.add_transaction(txn.description, txn.amount, txn.category, next_occurrence, "RECUR:%s:%s" % (txn.dedupe_string, next_occurrence.isoformat()))
				self.db.process_recurring_transaction_completion(txn.id)
			txns = self.db.get_past_due_recurring_transactions()
			logger.info("%s recurring transactions to process in next iteration" % len(txns))
