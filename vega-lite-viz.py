# import altair with an abbreviated alias
import altair as alt

import datetime

#data = 'knowledge_cleaned_for_tdm.csv'

# make the chart
import altair as alt
from vega_datasets import data

source = 'extended_diss.csv'

#''' # vis across years bubble plot

dis = alt.Chart(source).mark_circle(
		opacity=0.8,
		stroke='black',
		strokeWidth=1,
		strokeOpacity=0.4
	).encode(
		alt.X('Degree Date:T')
			.scale(domain=['1890','2025']),
		alt.Y('Department:N')
			.sort("-size", op="sum", order='descending'),
		alt.Size('count():Q'),
		alt.Color('Department:N'),
		tooltip=[
        "Department:N",
        alt.Tooltip("Degree Date:T", format='%Y'),
        alt.Tooltip("count():Q", format='~s')
    ],
	).properties(
		width=1000,
		height=1000,
		title=alt.Title(
			text="UChicago Dissertations (1890-2025)",
			subtitle="The size of the bubble represents the total dissertation count per year, by dissertation department",
			anchor='start'
		)
	).configure_axisY(
		domain=False,
		ticks=False,
		offset=10
	).configure_axisX(
		grid=False,
	).configure_view(
		stroke=None
	)

#'''

'''
selection = alt.selection_point(fields=['series'], bind='legend')

dis = alt.Chart(source).mark_area().encode(
    alt.X('Date:T').axis(domain=False,tickSize=0).scale(domain=['2015','2025']),
    alt.Y('count():Q').stack('center').axis(None),
    alt.Color('Paper Categories:N').scale(scheme='category20b'),
    tooltip=[
        "Paper Categories:N",
        alt.Tooltip("Date:T", format='%Y'),
        alt.Tooltip("count():Q", format='~s')
    ],
    opacity=alt.when(selection).then(alt.value(1)).otherwise(alt.value(0.2))
).transform_filter(
    #alt.FieldLTPredicate(field='Date:T', lt="2015-06")
    alt.datum.Date < "2025-06"
).add_params(
    selection
)
'''

dis.save('vis.html')