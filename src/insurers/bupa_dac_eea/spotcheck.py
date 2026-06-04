"""One-off spot-check script — verify parser against insurer-confirmed values."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.insurers.bupa_dac_eea.rate_parser import parse_bupa_dac_eea_rates


def main():
    ticket = Path('/home/support/Desktop/MIRO/auto-onboarding/PROD-1968-PlanUpdate–BUPADAC(Global Health)–ROW')
    data = parse_bupa_dac_eea_rates(ticket)
    print(f'Plans: {data["plans_seen"]}')
    print(f'Raw pages: {len(data["raw_pages"])}')
    print(f'Rate keys: {len(data["rates"])}')
    print(f'Warnings: {len(data["warnings"])}')

    # Insurer's confirmed values at age 30 (€)
    confirmed = {
        ('Major Medical', 'Worldwide excluding USA', 0, None):     1464.78,
        ('Major Medical', 'Worldwide', 0, None):                   3333.85,
        ('Select',        'Europe (Including UK)',  0, 0):         3205.58,
        ('Select',        'Worldwide excluding USA', 0, 0):        3385.09,
        ('Select',        'Worldwide',               0, 0):        7257.02,
        ('Premier',       'Europe (Including UK)',  0, 0):         5553.07,
        ('Premier',       'Worldwide excluding USA', 0, 0):        5865.02,
        ('Premier',       'Worldwide',               0, 0):        13356.59,
        ('Elite',         'Europe (Including UK)',  0, 0):         9191.30,
        ('Elite',         'Worldwide excluding USA', 0, 0):        9707.69,
        ('Elite',         'Worldwide',               0, 0):        22112.92,
        ('Ultimate',      'Worldwide excluding USA', 0, 0):        19920.91,
        ('Ultimate',      'Worldwide',               0, 0):        45381.68,
    }
    print()
    print('SPOT-CHECK against insurer-confirmed (age 30, Zone 8 for non-WW; Zone 1 for WW):')
    print(f'{"Plan":<14} {"Coverage":<25} {"Expected (EUR)":<15} {"Got":<15} Match')
    print('-' * 90)
    ok = bad = 0
    for (plan, cov, ip, op), expected in confirmed.items():
        zone = 1 if cov == 'Worldwide' else 8
        key = (zone, plan, cov, 30, ip, op, 'Annually')
        entry = data['rates'].get(key)
        got = entry['EUR'] if entry else None
        if got is None:
            print(f'{plan:<14} {cov:<25} {expected:<15.2f} (no match)      MISSING')
            bad += 1
        else:
            ok_match = abs(got - expected) < 0.5
            match = 'OK' if ok_match else f'DELTA {got-expected:+.2f}'
            print(f'{plan:<14} {cov:<25} {expected:<15.2f} {got:<15.2f} {match}')
            if ok_match:
                ok += 1
            else:
                bad += 1
    print(f'\nTotal: {ok}/{ok+bad} matched')
    print(f'\nFirst 5 warnings:')
    for w in data['warnings'][:5]:
        print(f'  - {w}')


if __name__ == '__main__':
    main()
