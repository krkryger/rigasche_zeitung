import sys
from unittest import result
import pandas as pd
import re
import convertdate
import json
from tqdm import tqdm

datapath = sys.argv[1]

print('Importing raw data')
main_df = pd.read_parquet(datapath)

print('Loading auxiliaries')
with open('../data/re_pattern2.txt', 'r', encoding='utf8') as f:
    pattern = re.compile(f.read(), flags=re.MULTILINE)

with open('../data/re_exceptions.txt', 'r', encoding='utf8') as f:
    exceptions = []
    for line in f.readlines():
        exceptions.append(line.strip('\n'))

with open('../data/placename_replacement_dict.json', 'r', encoding='utf8') as f:
    placename_replacement_dict = json.load(f)


def extract_data_from_match(match):
    m = match.groupdict()
    if m['DATE2'] is None:
        return [m['placename'], m['date'], m['date_par'], m['month'], m['month2'], m['year'],
                match.span()[0], match.span()[1]]
    elif m['DATE'] is None:
        return [m['placename'], m['date2'], m['date2_par'], m['month2'], m['month2_par'], m['year'],
                match.span()[0], match.span()[1]]

        
def scan_placenames_dates(main_df, exceptions):
    
    results = []
    
    for ix, row in tqdm(main_df.iterrows()):
        matches = list(re.finditer(pattern, row.full_text))
        for m in matches:
            results.append([ix, row.date] + extract_data_from_match(m))
        
    result_df = pd.DataFrame(columns=['doc_id',
                                      'doc_date',
                                      'placename',
                                      'day',
                                      'day2',
                                      'month',
                                      'month2',
                                      'origin_year',
                                      'start',
                                      'end'],
                             data=results).fillna(pd.NA)
    
    result_df['doc_date'] = pd.to_datetime(result_df['doc_date'], format='%Y-%m-%d')
    #result_df['year'] = result_df['doc_date'].dt.year

    result_df['origin_year'].fillna(0, inplace=True)
    result_df['origin_year'] = result_df['origin_year'].astype(int)
    result_df['origin_year'].replace(0, pd.NA, inplace=True)
    
    for col in ['day', 'day2']:
        
        result_df[col] = result_df[col].str.lstrip('( \n').str.strip('.)\n ')
        result_df[col].fillna(0, inplace=True)
        result_df[col] = result_df[col].astype(int)
        result_df[col] = result_df[col].apply(lambda x: x if (x in range(1,32)) else pd.NA)

        # try/except included because of possible pandas bug: AttributeError: 'bool' object has no attribute 'to_numpy'
        try:
            result_df[col].replace(0, pd.NA, inplace=True)
        except AttributeError:
            result_df.loc[(result_df[col] == 0), col] = pd.NA

    # return the entries where placename does not fall under exceptions
    return result_df[~result_df.placename.isin(exceptions)]


def cleanup_dates(df):

    """Formats the day/month columns for further operations and removes noise."""

    month_dict = dict(zip(['Jan', 'Feb', 'M??r', 'Apr', 'Mai', 'Jun',
                           'Jul', 'Aug', 'Sept', 'Okt', 'Nov', 'Dec'], range(1,13)))
    
    for col in ['month', 'month2']:
    
        df[col] = df[col].str.capitalize()
        df[col] = df[col].str.capitalize()
        df[col].replace({'Dez': 'Dec', 'Mar': 'M??r', 'Oct': 'Okt', '0ct': 'Okt', '0kt': 'Okt', 'Ocl': 'Okt',
                         'Spt': 'Sept', 'Jnl': 'Jul', 'Jnn': 'Jun', 'Juu': 'Jun', 'Jnu': 'Jun', 'May': 'Mai'}, inplace=True)

        df[col].replace(month_dict, inplace=True)
        df.loc[df[col].isin(['C', 'C.', 'D. m', 'D. m.']), col] = df.loc[df[col].isin(['C', 'C.', 'D. m', 'D. m.']), 'doc_date'].dt.month
        df.loc[df[col].isin(['V. m', 'V. m.']), 'month'] = (df.loc[df[col].isin(['v. M', 'V. m.']), 'doc_date'] - pd.DateOffset(months=1)).dt.month - 1
        
        df[col] = df[col].fillna(0).astype(int)
        df[col] = df[col].apply(lambda x: x if (x in range(1,13)) else pd.NA)
    
    return df    


def verify_dates(ix, df):
    
    """Function to find the logically correct date from the information found by the regular expression.
    Has different approaches depending on the number of dates and months detected. In the case of multiple
    possibilities, returns the earlier one (corresponding to the Julian calendar)."""
    
    #print(df.loc[ix,['day', 'day2', 'month', 'doc_date']].values)
    
    day, day2, month, month2, origin_year, doc_date = [value if type(value) != pd._libs.missing.NAType else None
                                          for value in df.loc[ix,['day', 'day2', 'month', 'month2', 'origin_year', 'doc_date']].values]
    
    path = []
    
    # if 
    if day is None or month is None:
        path.append(0)
        return pd.NA
    
    ### two dates, two months
    if None not in [day, day2, month, month2]:
        path.append(1)
        
        if origin_year:
            origin_date = min(
                pd.to_datetime(f'{str(origin_year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce'),
                pd.to_datetime(f'{str(origin_year)}-{month2}-{day2}', format='%Y-%m-%d', errors='coerce')
            )
            
        # exception for december/january  
        elif month == 1 and month2 == 12:
            if doc_date.month == 12:
                return pd.to_datetime(f'{str(doc_date.year)}-{month2}-{day2}', format='%Y-%m-%d', errors='coerce')
            else: 
                return pd.to_datetime(f'{str(doc_date.year-1)}-{month2}-{day2}', format='%Y-%m-%d', errors='coerce')
                        
        # if both months come after publication month, news is probably from last year
        elif month > doc_date.month and month2 > doc_date.month:
            
                path.append(1.1)
                possible_origin_dates = []
                for d, m in zip([day, day2], [month, month2]):
                    origin_year = doc_date.year - 1
                    possible_origin_dates.append(
                            pd.to_datetime(f'{str(origin_year)}-{m}-{d}', format='%Y-%m-%d', errors='coerce'))
                        
                return min(possible_origin_dates)
        
        # normal proceeding
        else:
            path.append(1.2)
            # julian is the smaller one
            return min(
                pd.to_datetime(f'{str(doc_date.year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce'),
                pd.to_datetime(f'{str(doc_date.year)}-{month2}-{day2}', format='%Y-%m-%d', errors='coerce')
            )
       
    
    ### two dates, one month
    elif day and day2 and month:
        path.append(2)
        
        if origin_year:
            return min(
                pd.to_datetime(f'{str(origin_year)}-{month}-{day2}', format='%Y-%m-%d', errors='coerce'),
                pd.to_datetime(f'{str(origin_year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce')
            )
        
        # if month is the same or precedes the publication month
        elif month == doc_date.month or month < doc_date.month:
            path.append(2.1)
            
            # julian is the smaller one
            return min(
                pd.to_datetime(f'{str(doc_date.year)}-{month}-{day2}', format='%Y-%m-%d', errors='coerce'),
                pd.to_datetime(f'{str(doc_date.year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce')
            )
       
        # if month follows publication month
        elif month > doc_date.month:
            path.append(2.2)
            
            # formulate dates for both possibilities
            origin_date = min(
                pd.to_datetime(f'{str(doc_date.year)}-{month}-{day2}', format='%Y-%m-%d', errors='coerce'),
                pd.to_datetime(f'{str(doc_date.year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce'))
             
            # is the smaller one more than 12 days later than publication date?
            if origin_date - doc_date > pd.Timedelta(days=12):
                # so the news is from last year
                return origin_date - pd.DateOffset(years=1)
            else:
                # if not, it is probably a georgian date a few days ahead of the julian publication date
                try:
                    day_jul = convertdate.julian.from_gregorian(year=origin_date.year,
                                                                    month=origin_date.month, day=origin_date.day)
                except ValueError:
                    return pd.NA
                
                return pd.to_datetime(f'{str(day_jul[0])}-{str(day_jul[1])}-{str(day_jul[2])}',
                                             format='%Y-%m-%d', errors='coerce')

            
    # one day, one month
    elif day and month:
        path.append(3)            
        
        # if month precedes publication month
        if month < doc_date.month:
            path.append(3.1)
            # (probably) julian
            return pd.to_datetime(f'{origin_year if origin_year else str(doc_date.year)}-{month}-{day}',
                                         format='%Y-%m-%d', errors='coerce')
        
        # if month is the same as publication month
        elif month == doc_date.month:
            path.append(3.2)
            
            # if the day is later than the publication day, within the same month
            if day > doc_date.day:
                path.append(3.21)
                
                # origin is gregorian, convert to julian first, then apply
                try:
                    day_jul = convertdate.julian.from_gregorian(year=origin_year if origin_year else doc_date.year,
                                                                    month=month, day=day)
                except ValueError:
                    return pd.NA
                
                return pd.to_datetime(f'{str(day_jul[0])}-{str(day_jul[1])}-{str(day_jul[2])}',
                                             format='%Y-%m-%d', errors='coerce')
            
            # if the day precedes the publication day, within the same month
            else:
                path.append(3.22)
                # origin is (probably) julian
                return pd.to_datetime(f'{origin_year if origin_year else str(doc_date.year)}-{month}-{day}',
                                             format='%Y-%m-%d', errors='coerce')
        
        
        # if month is greater than publication month
        elif month > doc_date.month:
            path.append(3.3)
            
            if origin_year:
                return pd.to_datetime(f'{origin_year}-{month}-{day}', format='%Y-%m-%d', errors='coerce')
                
            else:
                origin_date = pd.to_datetime(f'{str(doc_date.year)}-{month}-{day}', format='%Y-%m-%d', errors='coerce')
             
                # is the difference more than 12 days compared to the publication date?
                if origin_date - doc_date > pd.Timedelta(days=12):
                    # so the news is from last year
                    return origin_date - pd.DateOffset(years=1)
                else:
                    # if not, it is probably a georgian date a few days ahead of the julian publication date
                    try:
                        day_jul = convertdate.julian.from_gregorian(year=origin_date.year,
                                                                        month=origin_date.month, day=origin_date.day)
                    except ValueError:
                        return pd.NA
                
                    return pd.to_datetime(f'{str(day_jul[0])}-{str(day_jul[1])}-{str(day_jul[2])}',
                                                 format='%Y-%m-%d', errors='coerce')


print('Scanning raw data for placenames and dates')
df = scan_placenames_dates(main_df, exceptions)

df.placename.replace(placename_replacement_dict, inplace=True)

print('Formatting date info')
df = cleanup_dates(df)

print('Calculating origin dates and calendar differences')
origin_dates = []
for ix in tqdm(df.index):
    origin_dates.append(verify_dates(ix, df))

df['origin_date'] = origin_dates
df.dropna(subset=['origin_date'], inplace=True) # drop entries with no valid date found
df['origin_date'] = pd.to_datetime(df.origin_date)

df['delta'] = (df['doc_date'] - df['origin_date']).dt.days

# drop entries with origin date that is negative or more than 1 year
print(f'Dropping {len(df) - len(df.loc[(df.delta > 0) & (df.delta < 350)])} entries with invalid (x < 0 | x > 350) time differences')
#df = df.loc[(df.delta > 0) & (df.delta < 350)] 

print('Saving the dataframe')
df.to_csv('../data/processed_data.tsv', sep='\t', encoding='utf8', index=False)

print('Finished')

