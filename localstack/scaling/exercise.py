#!/usr/bin/env python3
"""Bounded purchase exercise through authenticated API; uses the normal publisher/Pipe."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'business'))
from business import Lab, endpoint, token, purchase, http_request, pdf_proof, pipe, publisher_schedule, report_dir, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=endpoint, default='http://100.100.154.68:4566')
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--rate', type=float, default=1)
    args = parser.parse_args()
    if not 1 <= args.count <= 30 or not math.isfinite(args.rate) or not 0 < args.rate <= 5:
        parser.error('Use 1..30 purchases and 0 < rate <= 5 per second')
    lab = Lab(args.endpoint)
    if pipe(lab)['CurrentState'] != 'RUNNING' or publisher_schedule(lab)['State'] != 'ENABLED':
        raise ValueError('Publisher and Pipe must already be enabled; exercise does not change trigger state')
    bearer = token(lab)
    path = report_dir() / 'scaling-exercise.json'
    proof = {'status': 'RUNNING', 'requestedAcceptanceRate': args.rate, 'purchases': [],
             'note': 'Acceptance pacing is not workflow start-rate control; publisher may release a batch.'}
    try:
        for _ in range(args.count):
            started = time.monotonic()
            _, accepted = purchase(lab, bearer)
            proof['purchases'].append({'saleId': accepted['saleId'], 'eventId': accepted['eventId'],
                                       'acceptanceMilliseconds': round(1000 * (time.monotonic() - started), 2)})
            save(path, proof)
            # No catch-up bursts after slow requests.
            time.sleep(max(0, 1 / args.rate - (time.monotonic() - started)))
        pending = list(proof['purchases'])
        deadline = time.monotonic() + 240
        while pending and time.monotonic() < deadline:
            for item in list(pending):
                status, sale = http_request(lab, '/purchases/' + item['saleId'], bearer=bearer)
                if status != 200:
                    raise RuntimeError(f'Purchase status HTTP {status}')
                if sale['status'] in ('COMPLETED', 'FAILED'):
                    item.update(status=sale['status'], executionArn=sale.get('executionArn'))
                    if sale['status'] == 'COMPLETED':
                        item['pdf'] = pdf_proof(lab, sale)
                    pending.remove(item)
            if pending:
                time.sleep(3)
        for item in pending:
            item['status'] = 'TIMEOUT'
        proof['status'] = 'PASS' if all(x['status'] == 'COMPLETED' for x in proof['purchases']) else 'FAIL'
    except Exception as exc:
        proof.update(status='FAIL', error=str(exc)[:1000])
        raise
    finally:
        save(path, proof)
        print(json.dumps(proof, indent=2))
        print('Report: ' + str(path))
    if proof['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
