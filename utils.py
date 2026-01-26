import csv
import re
import json
from ast import literal_eval

def load_csv(file):
	data = []
	with open(file, "r", encoding="utf-8") as f:
		csvFile = csv.reader(f)
		for line in csvFile:
			data.append(line)
			
	return data

def department_stats(data):
	dept_dict = {}

	for entry in data:
		if entry[9][0] == '[':
			dept_list = literal_eval(entry[9])
			for dept in dept_list:
				if dept not in dept_dict.keys():
					dept_dict[dept] = 1
				else:
					dept_dict[dept] += 1

			if len(dept_list) == 0:
				# accounting for some cases where no department is listed just the school (Harris, Law, PME)
				dept_list = literal_eval(entry[11])
				for dept in dept_list:
					if dept not in dept_dict.keys():
						dept_dict[dept] = 1
					else:
						dept_dict[dept] += 1

	sorted_dict = dict(sorted(dept_dict.items(), key= lambda item: item[1]))
	for dept in sorted_dict:
		print(dept)
		print(sorted_dict[dept])

def json_from_csv(csv_file, json_file):
	csvfile = open(csv_file, 'r')
	jsonfile = open(json_file, 'w')

	fieldnames = ("Title","Date","Authors","Advisors","Committee Members","Department","Paper Categories","Paper Keywords")
	reader = csv.DictReader( csvfile, fieldnames)
	next(reader)
	out = json.dumps( [ row for row in reader ] )
	jsonfile.write(out)


if __name__ == "__main__":
	data = load_csv("knowledge_cleaned_for_tdm.csv")

	department_stats(data)


	#json_from_csv('2025_diss.csv', '2025_diss.json')