import logging
import os

import requests
import json

from dotenv import load_dotenv
from tqdm import tqdm

from langchain_core.documents import Document

import utils


class ApiConnectorEntwickleraufgaben():

    def __init__(self, group_id: int):
        load_dotenv()
        self.BITRIX_SECRET = os.environ["BITRIX_SECRET"]
        self.WEBHOOK_URL_TASKS = f"https://workspace.linxys.com/rest/1377/{self.BITRIX_SECRET}/task.item.getlist.json"
        self.no_id_cnt = -1
        self.group_id = group_id
        self.logger = logging.getLogger(__name__)

    def __save_latest_activity_date(self, activity_date):
        # Save the latest activity date to a file
        with open('latest_activity_date.txt', 'w') as file:
            file.write(activity_date)

    def __get_latest_activity_date(self):
        # Check if the file with the latest activity date exists and read it
        if os.path.exists('latest_activity_date.txt'):
            with open('latest_activity_date.txt', 'r') as file:
                return file.read().strip()
        return None

    def run(self):
        self.logger.info("Starting ApiConnectorEntwickleraufgaben...")
        headers = {'Content-Type': 'application/json'}
        all_tasks = []  # List to hold all tasks
        page = 1  # Starting page
        total_tasks = None  # Total number of tasks, to be fetched from the first response
        latest_activity_date = self.__get_latest_activity_date()  # Load the latest activity date from file

        with tqdm(total=total_tasks if total_tasks else 100, desc="Fetching tasks", unit="task") as pbar:
            while True:
                pagesize = 50
                data = {
                    'ORDER': {"ACTIVITY_DATE": "desc"},
                    'FILTER': {"GROUP_ID": self.group_id},
                    'PARAMS': {'NAV_PARAMS': {"nPageSize": pagesize, "iNumPage": page}},
                    'SELECT': ["*"]
                }

                self.logger.info(f"Reading Page {page}...")

                json_data = json.dumps([data['ORDER'], data['FILTER'], data['PARAMS'], data['SELECT']])
                response = requests.post(self.WEBHOOK_URL_TASKS, headers=headers, data=json_data)

                if response.ok:
                    # Assuming response.json() returns a dictionary with 'result' holding the tasks
                    json_response = response.json()
                    tasks_result = json_response.get('result', [])  # Extract tasks

                    self.logger.info(f"Fetched entries {page * pagesize} successfully. Total: {json_response.get('total', 0)}")

                    if total_tasks is None:
                        total_tasks = json_response.get('total', 0)
                        pbar.total = total_tasks

                    # Process and filter tasks by activity date
                    new_tasks = []
                    for task in tasks_result:
                        task_activity_date = task.get('ACTIVITY_DATE', '')
                        if latest_activity_date and task_activity_date <= latest_activity_date:
                            return  # Stop processing if task is older than the recorded activity date
                        new_tasks.append(task)

                    if page == 1 and tasks_result:
                        # Save the activity date of the first task for the next run
                        self.__save_latest_activity_date(tasks_result[0].get('ACTIVITY_DATE', ''))

                    # Sanitize task data
                    sanitized_tasks = self.__sanitize_task(new_tasks)
                    all_tasks.extend(sanitized_tasks)

                    pbar.update(len(tasks_result))

                    if len(all_tasks) >= total_tasks or not json_response.get('result'):
                        break  # Exit the loop if all tasks have been fetched or no tasks are returned
                    page += 1  # Increment the page number for the next iteration
                else:
                    print(f"Failed to get tasks. Status code: {response.status_code}\n{response.json()}")
                    break
        return all_tasks

    def __sanitize_task(self, response_data): # Nur Einträge der letzten 3 Jahre
        sanitized_tasks = []
        for task in response_data:
            sanitized_task = {}

            if 'ID' not in task:
                sanitized_task['ID'] = f'{self.no_id_cnt}'
                self.no_id_cnt = self.no_id_cnt - 1
            else:
                sanitized_task['ID'] = task['ID']

            # TITLE: Split by ": " to get the customizing title without the company name.
            # Use the whole title if split fails.
            title = task.get('TITLE', '')
            sanitized_task['TITLE'] = title

            # DESCRIPTION: Remove everything after a specific pattern. Use the whole description if pattern not found.
            description = task.get('DESCRIPTION', '')
            split_description = description.split("[B][SIZE=10pt]Access to the System:[/SIZE][/B]", 1)
            sanitized_task['DESCRIPTION'] = split_description[0] if len(split_description) > 1 else description

            # RESPONSIBLE_NAME: Merge RESPONSIBLE_NAME and RESPONSIBLE_LAST_NAME.
            responsible_name = task.get('RESPONSIBLE_NAME', '')
            responsible_last_name = task.get('RESPONSIBLE_LAST_NAME', '')
            sanitized_task['RESPONSIBLE_NAME'] = f"{responsible_name} {responsible_last_name}".strip()

            # ACTIVITY_DATE
            sanitized_task['ACTIVITY_DATE'] = task.get('ACTIVITY_DATE', '')

            # TIME_SPENT_IN_LOGS: Convert seconds to "Xh Ymin" format if not None.
            time_spent = task.get('TIME_SPENT_IN_LOGS', 'None')
            # Check if time_spent is not None and not the string 'Not recorded'
            if time_spent not in [None, 'None']:
                # Ensure time_spent is an integer before conversion
                time_spent_int = int(time_spent)
                hours, remainder = divmod(time_spent_int, 3600)
                minutes, _ = divmod(remainder, 60)
                sanitized_task['TIME_SPENT_IN_LOGS'] = f"{hours}h {minutes}min" if hours or minutes else '0min'
            else:
                sanitized_task['TIME_SPENT_IN_LOGS'] = 'Not recorded'

            # Determine system metadata based on keywords in description
            system_metadata = None
            if any(keyword in description.lower() for keyword in ["on premise", "on-premise"]):
                system_metadata = "On-Premise"
            elif "cloud" in description.lower():
                system_metadata = "Cloud"

            url = f"https://workspace.linxys.com/workgroups/group/74/tasks/task/view/{sanitized_task['ID']}/"

            page_content = f"Time spent: {sanitized_task['TIME_SPENT_IN_LOGS']}\n" \
                           f"Description: {sanitized_task['DESCRIPTION']}\n"
            doc = Document(page_content=page_content, metadata={
                "source": url,
                "name": sanitized_task['TITLE'],
                "system": system_metadata
            })

            # Save the document to a .pixo file
            utils.save_document(doc=doc, filename=f"{sanitized_task['ID']}.pixodoc")

            sanitized_tasks.append(sanitized_task)
        return sanitized_tasks


if __name__ == "__main__":
    processor = ApiConnectorEntwickleraufgaben(group_id=74)
    processor.run()
