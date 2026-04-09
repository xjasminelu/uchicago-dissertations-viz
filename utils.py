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

def json_dept_diss_years(csv_file, data_dump_file):
	csvfile = open(csv_file, 'r')

	dept_diss_years = {}

	fieldnames = ("GOID","Title","Date","Source Type","Authors","Language","Pages","Advisors","Committee Members","Department","Subject Terms","Paper Categories","Paper Keywords")
	reader = csv.DictReader( csvfile, fieldnames)
	next(reader)
	for row in reader:
		diss_depts = row['Department']
		year = row['Degree Date'].split('-')[0]

		if year in dept_diss_years.keys():
			for diss_dept in literal_eval(diss_depts):
				if diss_dept in dept_diss_years[year].keys():
					dept_diss_years[year][diss_dept] += 1
				else:
					dept_diss_years[year][diss_dept] = 1

			if len(literal_eval(diss_depts)) == 0:
				diss_div = literal_eval(row['Paper Categories'])[0]
				if diss_div in dept_diss_years[year].keys():
					dept_diss_years[year][diss_div] += 1
				else:
					dept_diss_years[year][diss_div] = 1
		else:
			dept_diss_years[year] = {}
			for diss_dept in literal_eval(diss_depts):
				dept_diss_years[year][diss_dept] = 1
			if len(literal_eval(diss_depts)) == 0:
				diss_div = literal_eval(row['Paper Categories'])[0]
				if diss_div in dept_diss_years[year].keys():
					dept_diss_years[year][diss_div] += 1
				else:
					dept_diss_years[year][diss_div] = 1


	with open(data_dump_file, 'w', newline='') as csvfile:
		writer = csv.writer(csvfile)
		writer.writerow(['Year', 'Department', 'Dissertations'])

		for k,v in dept_diss_years.items():
			for kk, vv in v.items():
				writer.writerow([])

def hathi_to_csv(page_txt, write_csv, school_division, department):

	entries = []
	row = []
	txt_split = page_txt.split('.')
	key = ''
	cell = ''

	for txt in txt_split:
		print(txt)

		for sec in txt.split(','):
			
			if key == 'e':
				key = input()
				if key == 'r':
						
					row.append(cell)
					entries.append(row)
					print(row)

					cell = ''
					row = []
				

			continue_flag = False

			for word in sec.split(' '):
				print(word)
				print(txt)
				print('r - new row, c - continue, n - new cell, i - ignore, e - continue until , or . split')

				if not continue_flag:
					key = input()
					if key == 'r':
						
						row.append(cell)
						entries.append(row)
						print(row)

						cell = ''
						row = []
					elif key == 'c':
						if len(cell) != 0:
							cell = cell + ' ' + word
						else:
							cell += word
					elif key == 'n':
						if len(cell) != 0:
							cell = cell + ' ' + word
						else:
							cell += word
						row.append(cell)
						cell = ''
					elif key == 'i':
						continue
					elif key == 'e':
						if len(cell) != 0:
							cell = cell + ' ' + word
						else:
							cell += word
						continue_flag = True

				else:
					if len(cell) != 0:
						cell = cell + ' ' + word
					else:
						cell += word

	with open(write_csv, 'w', newline='') as csvfile:
		writer = csv.writer(csvfile)
		#writer.writerow(['Year', 'Division', 'Department', 'Name', 'Diss_Title', 'Post'])

		for entry in entries:
			writer.writerow([entry[0], school_division, department, entry[1], entry[2], entry[3]])

def hathi_to_csv2(page_txt, write_csv, school_division, department):
	
	with open(write_csv, 'a', newline='') as csvfile:
		writer = csv.writer(csvfile)
		#writer.writerow(['Year', 'Division', 'Department', 'Name', 'Diss_Title', 'Post'])

		for year_sec in page_txt.split('\n\n\n\n'):
			txt = year_sec.split('\n\n\n')
			year = txt[0]
			for entry in txt[1].split('\n\n'):
				split = entry.split('\n')
				writer.writerow([year, school_division, department, split[0], split[1], split[2]])


if __name__ == "__main__":
	#data = load_csv("knowledge_cleaned_for_tdm.csv")

	#department_stats(data)


	#json_from_csv('2025_diss.csv', '2025_diss.json')

	#json_dept_diss_years("knowledge_cleaned_for_tdm.csv", 'dept_diss_years.json')


	page_txt = '''1930


ELEANOR STUART UPTON
Senior Cataloger, Yale University Library, New Haven, Conn.
A Guide to Sources of Seventeenth-Century History in Selected Reports of the Historical Manuscripts Commission of Great Britain.



1931


JOHN CHI BER KWEI
23 Tang Hua Ling Street, Wuchang, China.
Bibliographical and Administrative Problems Arising from the Incorporation of Chinese Books in American Libraries.

'''
	write_csv = 'hathi_1893-1931.csv'
	school_division = 'THE PROFESSIONAL SCHOOLS'
	department = 'THE GRADUATE LIBRARY SCHOOL'
	year = 1905
	hathi_to_csv2(page_txt, write_csv, school_division, department)