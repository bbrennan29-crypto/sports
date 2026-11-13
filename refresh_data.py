#!/usr/bin/env python3
"""Download actual NFL player-week stats and Sleeper player directory (no API key).
Python 3.9+ standard library only. Does not invent or fill missing stats.
"""
import csv
import io
import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

YEARS = [2023, 2024, 2025, 2026]
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(parents=True, exist_ok=True)
BASE = 'https://github.com/nflverse/nflverse-data/releases/download/stats_player'
SLEEPER = 'https://api.sleeper.app/v1/players/nfl'
FIELDS = ['season','week','season_type','player_id','player_name','position','team','opponent_team',
          'completions','attempts','passing_yards','passing_tds','passing_interceptions',
          'carries','rushing_yards','rushing_tds','targets','receptions','receiving_yards',
          'receiving_tds','fumbles_lost','passing_2pt_conversions','rushing_2pt_conversions',
          'receiving_2pt_conversions','fantasy_points_standard','fantasy_points_half_ppr',
          'fantasy_points_ppr','source_url']

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'FantasyOptimizerStarter/1.0 (public data research)','Accept':'*/*'})
            with urllib.request.urlopen(req, timeout=65) as response:
                return response.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)

def num(row, field):
    try:
        return float(row.get(field) or 0)
    except (TypeError, ValueError):
        return 0.0

def val(row, *names):
    return next((str(row[n]) for n in names if row.get(n) not in (None,'')), '')

def roundstat(x):
    return round(float(x), 3)

def normalize(row, url):
    # nflverse sometimes uses different schema names across releases.
    year = val(row,'season'); week = val(row,'week')
    if not (year.isdigit() and week.isdigit()):
        return None
    season_type = val(row, 'season_type')
    if season_type not in ('REG','POST'):
        return None
    player = val(row,'player_display_name','player_name')
    position = val(row,'position')
    if not player or not position:
        return None
    n = {'season':int(year),'week':int(week),'season_type':season_type,
         'player_id':val(row,'player_id','player_gsis_id','gsis_id'),
         'player_name':player,'position':position,
         'team':val(row,'team','recent_team'),'opponent_team':val(row,'opponent_team'),
         'source_url':url}
    statkeys = [k for k in FIELDS if k not in n and not k.startswith('fantasy_points')]
    for key in statkeys:
        n[key] = roundstat(num(row,key))
    # Standard NFL fantasy convention: 1/25 passing, 4 passing TD, -2 INT,
    # 1/10 rush/receive, 6 rush/receive TD, -2 fumble lost, 2 per 2-point play.
    base = (num(row,'passing_yards')/25 + num(row,'passing_tds')*4
         - num(row,'passing_interceptions')*2 + num(row,'rushing_yards')/10
         + num(row,'rushing_tds')*6 + num(row,'receiving_yards')/10
         + num(row,'receiving_tds')*6 - num(row,'fumbles_lost')*2
         + 2*(num(row,'passing_2pt_conversions')+num(row,'rushing_2pt_conversions')
              +num(row,'receiving_2pt_conversions')))
    n['fantasy_points_standard'] = round(base,2)
    n['fantasy_points_half_ppr'] = round(base + num(row,'receptions')*.5,2)
    n['fantasy_points_ppr'] = round(base + num(row,'receptions'),2)
    return n

def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

def main():
    rows=[]; status=[]
    for year in YEARS:
        url = f'{BASE}/stats_player_week_{year}.csv'
        print(f'Fetching {year} nflverse: {url}', flush=True)
        try:
            b = fetch(url)
            source = csv.DictReader(io.StringIO(b.decode('utf-8-sig')))
            input_count = 0
            count = 0
            for r in source:
                input_count += 1
                normalized = normalize(r, url)
                if normalized and normalized['position'] in ('QB','RB','WR','TE'):
                    rows.append(normalized)
                    count += 1
            status.append({'source':'nflverse','year':year,'status':'ok','input_rows':input_count,'included_qb_rb_wr_te_rows':count,'url':url})
            print(f'  Imported {count:,} offensive player-week rows',flush=True)
        except Exception as ex:
            status.append({'source':'nflverse','year':year,'status':'FAILED','error':str(ex),'url':url})
            print(f'  FAILED: {ex}',flush=True)
    if rows:
        rows.sort(key=lambda r:(r['season'],r['week'],r['player_name'],r['team']))
        write_csv(DATA/'player_weekly_master.csv',rows,FIELDS)
        agg = defaultdict(lambda: {'games':0,'points':0.0,'receptions':0.0,'targets':0.0,'carries':0.0})
        for r in rows:
            if r['season_type'] != 'REG': continue
            key=(r['season'],r['player_id'],r['player_name'],r['position'],r['team'])
            a=agg[key]; a['games']+=1; a['points']+=r['fantasy_points_ppr'];a['receptions']+=r['receptions'];a['targets']+=r['targets'];a['carries']+=r['carries']
        seasons=[]
        for (season,pid,name,pos,team), a in sorted(agg.items()):
            seasons.append({'season':season,'player_id':pid,'player_name':name,'position':pos,'team':team,
                            'games':a['games'],'total_ppr':round(a['points'],2),
                            'ppr_per_game':round(a['points']/a['games'],2),'targets':a['targets'],
                            'receptions':a['receptions'],'carries':a['carries']})
        write_csv(DATA/'player_season_summary.csv',seasons,
                  ['season','player_id','player_name','position','team','games','total_ppr','ppr_per_game','targets','receptions','carries'])
    else:
        print('No NFL player-week data retrieved; no empty master files created.',flush=True)
    print('Fetching Sleeper player directory...',flush=True)
    try:
        records=json.loads(fetch(SLEEPER))
        players=[]
        for sid,r in records.items():
            if not isinstance(r,dict) or r.get('position') not in ('QB','RB','WR','TE','K','DEF'): continue
            players.append({'sleeper_id':sid,'gsis_id':r.get('gsis_id') or '',
                'full_name':r.get('full_name') or '', 'position':r.get('position') or '',
                'team':r.get('team') or '', 'status':r.get('status') or '',
                'injury_status':r.get('injury_status') or '',
                'practice_participation':r.get('practice_participation') or '',
                'age':r.get('age') or '', 'number':r.get('number') or ''})
        players.sort(key=lambda r:(r['full_name'],r['sleeper_id']))
        write_csv(DATA/'sleeper_players.csv',players,list(players[0]) if players else ['sleeper_id'])
        status.append({'source':'Sleeper','status':'ok','player_count':len(players),'url':SLEEPER})
        print(f'  Imported {len(players):,} Sleeper player records',flush=True)
    except Exception as ex:
        status.append({'source':'Sleeper','status':'FAILED','error':str(ex),'url':SLEEPER})
        print(f'  FAILED: {ex}',flush=True)
    meta={'refreshed_utc':datetime.now(timezone.utc).isoformat(), 'scope':'2023-2026 QB/RB/WR/TE, regular + postseason rows, excludes K/DEF from scoring',
          'scoring':'Standard: 0.04/pass yard, 4/pass TD, -2/INT, 0.1/rush & rec yard, 6/rush & rec TD, -2/fumble lost, 2/2-point conversion; +0.5 or +1 per reception',
          'sources':status}
    (DATA/'refresh_status.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print('Status report: data/refresh_status.json',flush=True)
    if not rows:
        raise SystemExit('No nflverse data downloaded: see refresh_status.json for network/source errors')

if __name__ == '__main__':
    main()
