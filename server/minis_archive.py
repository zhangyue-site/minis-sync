import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time

GIB = 1024 ** 3
DEFAULT_POLICY = {
    'bucket': 'zy-archive-1302243082',
    'region': 'ap-shanghai',
    'max_cos_bytes': 8 * GIB,
    'recent_per_account': 3,
    'daily_request_limit': 12000,
    'annual_budget_yuan': 50,
    'historical_cost_reserve_yuan': 5,
    'standard_gb_month_yuan': 0.118,
    'request_yuan': 0.000001,
    'max_server_download_bytes_month': 150 * GIB,
    'lighthouse_instance_id': 'lhins-ftyxd23o',
    'server_traffic_reserve_bytes': 50 * GIB,
    'state_root': '/opt/notes-site/minis-archive',
    'prune_local_after_nas': False,
}


def policy():
    result = dict(DEFAULT_POLICY)
    path = Path(os.environ.get('MINIS_ARCHIVE_POLICY', '/opt/notes-site/minis-archive-policy.json'))
    if path.exists():
        result.update(json.loads(path.read_text()))
    return result


def cos_internal_client(settings):
    from qcloud_cos import CosConfig, CosS3Client
    credentials = json.loads(Path('/root/.tccli/default.credential').read_text())
    return CosS3Client(CosConfig(
        Region=settings['region'], SecretId=credentials['secretId'], SecretKey=credentials['secretKey'],
        Endpoint='cos-internal.' + settings['region'] + '.tencentcos.cn',
        AllowRedirects=False, AutoSwitchDomainOnRetry=False, Timeout=60))


class ArchiveManager:
    def __init__(self, store, settings=None, client=None):
        self.store = store
        self.settings = dict(DEFAULT_POLICY, **(settings or {}))
        self.root = Path(self.settings['state_root'])
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.root / 'archive.sqlite3'
        self.client = client
        with self.db() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    uid TEXT, sha TEXT, bytes INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0,
                    uploaded_at INTEGER, verified_at INTEGER, last_error TEXT,
                    PRIMARY KEY(uid,sha));
                CREATE TABLE IF NOT EXISTS nas_receipts (
                    uid TEXT, sha TEXT, bytes INTEGER NOT NULL, path TEXT NOT NULL,
                    verified_at INTEGER NOT NULL, PRIMARY KEY(uid,sha));
                CREATE TABLE IF NOT EXISTS requests (day TEXT PRIMARY KEY, reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS download_usage (month TEXT PRIMARY KEY, reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            connection.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('started_at', str(int(time.time()))))
        os.chmod(self.database, 0o600)

    def db(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def enqueue(self, uid, meta):
        uid = self.store._uid(uid)
        sha = str(meta['sha256'])
        if len(sha) != 64 or any(character not in '0123456789abcdef' for character in sha):
            raise ValueError('invalid_snapshot_digest')
        with self.db() as connection:
            connection.execute('INSERT OR IGNORE INTO jobs(uid,sha,bytes) VALUES(?,?,?)', (uid, sha, int(meta['bytes'])))

    def scan(self):
        with self.store._db() as connection:
            rows = connection.execute('SELECT user_id,sha256,bytes FROM snapshots ORDER BY created_at,sha256').fetchall()
        for row in rows:
            self.enqueue(row['user_id'], {'sha256': row['sha256'], 'bytes': row['bytes']})
        return len(rows)

    def receipt(self, uid, sha, size, path, verified_at):
        uid = self.store._uid(uid)
        try:
            source = self.store.path(uid, sha)
            valid = source.stat().st_size == size
        except FileNotFoundError:
            with self.db() as connection:
                previous = connection.execute('SELECT * FROM nas_receipts WHERE uid=? AND sha=?', (uid, sha)).fetchone()
                job = connection.execute('SELECT status FROM jobs WHERE uid=? AND sha=?', (uid, sha)).fetchone()
            valid = bool(previous and job and job['status'] == 'nas_only' and previous['bytes'] == size and previous['path'] == path)
        if not valid or not path.startswith('\\\\192.168.1.4\\公共空间\\Minis同步\\服务器完整快照\\'):
            raise ValueError('invalid_nas_receipt')
        if verified_at > time.time() + 300 or verified_at < time.time() - 86400:
            raise ValueError('stale_nas_receipt')
        with self.db() as connection:
            connection.execute('INSERT OR REPLACE INTO nas_receipts VALUES(?,?,?,?,?)', (str(uid), sha, size, path, verified_at))

    def reserve_requests(self, count):
        day = time.strftime('%Y-%m-%d', time.gmtime())
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT reserved FROM requests WHERE day=?', (day,)).fetchone()
            used = row[0] if row else 0
            annual = connection.execute('SELECT COALESCE(SUM(reserved),0) FROM requests WHERE day>=?', (time.strftime('%Y-%m-%d', time.gmtime(time.time() - 366 * 86400)),)).fetchone()[0]
            storage_ceiling = self.settings['max_cos_bytes'] / GIB * self.settings['standard_gb_month_yuan'] * 12.2
            projected = storage_ceiling + self.settings['historical_cost_reserve_yuan'] + (annual + count) * self.settings['request_yuan']
            if used + count > self.settings['daily_request_limit'] or projected >= self.settings['annual_budget_yuan']:
                raise RuntimeError('cos_request_budget_exhausted')
            connection.execute('INSERT OR REPLACE INTO requests VALUES(?,?)', (day, used + count))

    def reserve_download(self, length):
        month = time.strftime('%Y-%m-%d', time.gmtime())
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT reserved FROM download_usage WHERE month=?', (month,)).fetchone()
            today = row[0] if row else 0
            cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 31 * 86400))
            amount = connection.execute('SELECT COALESCE(SUM(reserved),0) FROM download_usage WHERE month>=?', (cutoff,)).fetchone()[0]
            if amount + length > self.settings['max_server_download_bytes_month']:
                return False
            if self.settings.get('lighthouse_instance_id'):
                cached = connection.execute('SELECT value FROM settings WHERE key=?', ('traffic_allowance',)).fetchone()
                allowance = json.loads(cached[0]) if cached else None
                if not allowance or time.time() - allowance['checked_at'] > 300:
                    try:
                        allowance = {'checked_at': int(time.time()), 'remaining': self.traffic_remaining(), 'reserved': 0}
                    except Exception:
                        logging.exception('Cannot verify prepaid server traffic; download paused')
                        return False
                if allowance['remaining'] - allowance['reserved'] - length < self.settings['server_traffic_reserve_bytes']:
                    return False
                allowance['reserved'] += length
                connection.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('traffic_allowance', json.dumps(allowance)))
            connection.execute('INSERT OR REPLACE INTO download_usage VALUES(?,?)', (month, today + length))
        return True

    def traffic_remaining(self):
        from tencentcloud.common import credential
        from tencentcloud.lighthouse.v20200324 import lighthouse_client, models
        credentials = json.loads(Path('/root/.tccli/default.credential').read_text())
        client = lighthouse_client.LighthouseClient(credential.Credential(credentials['secretId'], credentials['secretKey']), self.settings['region'])
        request = models.DescribeInstancesTrafficPackagesRequest()
        request.InstanceIds = [self.settings['lighthouse_instance_id']]
        response = client.DescribeInstancesTrafficPackages(request)
        timestamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        packages = [package for instance in response.InstanceTrafficPackageSet for package in instance.TrafficPackageSet
                    if package.StartTime <= timestamp < package.EndTime and package.Status == 'NETWORK_NORMAL']
        if not packages:
            raise RuntimeError('no_active_prepaid_traffic_package')
        return sum(package.TrafficPackageRemaining for package in packages)

    def objects(self):
        self.reserve_requests(1)
        result = {}
        marker = ''
        while True:
            page = self.cos().list_objects(Bucket=self.settings['bucket'], Prefix='minis-store/', Marker=marker, MaxKeys=1000)
            result.update({entry['Key']: int(entry['Size']) for entry in page.get('Contents', [])})
            if str(page.get('IsTruncated', '')).lower() != 'true':
                return result
            self.reserve_requests(1)
            marker = page['NextMarker']

    def cos(self):
        if self.client is None:
            self.client = cos_internal_client(self.settings)
        return self.client

    def keep_keys(self):
        with self.store._db() as connection:
            rows = connection.execute('SELECT user_id,sha256 FROM snapshots ORDER BY created_at DESC,sha256 DESC').fetchall()
        counts = {}
        keep = set()
        for row in rows:
            uid = row['user_id']
            counts[uid] = counts.get(uid, 0) + 1
            if counts[uid] <= self.settings['recent_per_account']:
                keep.add(self.key(uid, row['sha256']))
        return keep

    @staticmethod
    def key(uid, sha):
        return 'minis-store/' + str(uid) + '/' + sha + '.minisbak'

    def prune(self, objects, dry_run=False):
        keep = self.keep_keys()
        actions = []
        with self.db() as connection:
            receipts = connection.execute('SELECT * FROM nas_receipts').fetchall()
        for receipt in receipts:
            key = self.key(receipt['uid'], receipt['sha'])
            if key not in objects or key in keep or receipt['verified_at'] < time.time() - 86400:
                continue
            source = self.store.path(receipt['uid'], receipt['sha'])
            if source.stat().st_size != receipt['bytes']:
                raise RuntimeError('local_safety_copy_size_mismatch')
            if self.digest_file(source) != receipt['sha']:
                raise RuntimeError('local_safety_copy_digest_mismatch')
            actions.append({'key': key, 'bytes': objects[key], 'nas': receipt['path']})
            if not dry_run:
                self.reserve_requests(1)
                self.cos().delete_object(Bucket=self.settings['bucket'], Key=key)
                with self.db() as connection:
                    connection.execute('UPDATE jobs SET status=?,last_error=NULL WHERE uid=? AND sha=?', ('pruned_to_nas', receipt['uid'], receipt['sha']))
                objects.pop(key)
        return actions

    def prune_local(self, objects):
        if not self.settings['prune_local_after_nas']:
            return []
        keep = self.keep_keys()
        with self.db() as connection:
            receipts = connection.execute("SELECT n.*,j.status AS job_status FROM nas_receipts n JOIN jobs j ON j.uid=n.uid AND j.sha=n.sha WHERE j.status IN ('pruned_to_nas','nas_only')").fetchall()
        actions = []
        for receipt in receipts:
            key = self.key(receipt['uid'], receipt['sha'])
            if key in keep or key in objects or receipt['verified_at'] < time.time() - 900:
                continue
            try:
                source = self.store.path(receipt['uid'], receipt['sha'])
            except FileNotFoundError:
                if receipt['job_status'] == 'nas_only':
                    continue
                raise
            if source.stat().st_size != receipt['bytes'] or self.digest_file(source) != receipt['sha']:
                raise RuntimeError('local_prune_safety_mismatch')
            with self.db() as connection:
                connection.execute('UPDATE jobs SET status=? WHERE uid=? AND sha=?', ('nas_only', receipt['uid'], receipt['sha']))
            source.unlink()
            actions.append({'uid': receipt['uid'], 'sha256': receipt['sha'], 'bytes': receipt['bytes'], 'nas': receipt['path']})
        return actions

    @staticmethod
    def digest_file(path):
        digest = hashlib.sha256()
        with Path(path).open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()

    def run(self, limit=12):
        with (self.root / 'worker.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'already_running'}
            self.scan()
            objects = self.objects()
            deleted = self.prune(objects)
            completed = []
            with self.db() as connection:
                jobs = connection.execute('SELECT * FROM jobs WHERE status IN (?,?,?,?) AND retry_at<=? ORDER BY rowid DESC LIMIT ?', ('pending', 'retry', 'waiting_capacity', 'waiting_nas', int(time.time()), limit)).fetchall()
            for job in jobs:
                uid, sha = job['uid'], job['sha']
                try:
                    source = self.store.path(uid, sha)
                    if self.digest_file(source) != sha:
                        raise RuntimeError('source_digest_mismatch')
                    key = self.key(uid, sha)
                    size = source.stat().st_size
                    additional = 0 if key in objects else size
                    if sum(objects.values()) + additional > self.settings['max_cos_bytes']:
                        with self.db() as connection:
                            connection.execute('UPDATE jobs SET status=?,last_error=? WHERE uid=? AND sha=?', ('waiting_capacity', 'NAS proof required before reclaiming COS capacity', uid, sha))
                        continue
                    if key not in objects:
                        self.reserve_requests(5 * (math.ceil(size / (8 * 1024 * 1024)) + 10))
                        self.cos().upload_file(Bucket=self.settings['bucket'], Key=key, LocalFilePath=str(source), PartSize=8, MAXThread=2, Metadata={'sha256': sha})
                    self.reserve_requests(2)
                    head = self.cos().head_object(Bucket=self.settings['bucket'], Key=key)
                    if int(head['Content-Length']) != size:
                        raise RuntimeError('cos_size_mismatch')
                    response = self.cos().get_object(Bucket=self.settings['bucket'], Key=key)
                    digest = hashlib.sha256()
                    body = response['Body'].get_raw_stream()
                    with contextlib.closing(body):
                        for block in iter(lambda: body.read(1024 * 1024), b''):
                            digest.update(block)
                    if digest.hexdigest() != sha:
                        raise RuntimeError('cos_readback_digest_mismatch')
                    with self.db() as connection:
                        connection.execute('UPDATE jobs SET status=?,uploaded_at=?,verified_at=?,last_error=NULL WHERE uid=? AND sha=?', ('verified', int(time.time()), int(time.time()), uid, sha))
                    objects[key] = size
                    completed.append(key)
                    deleted.extend(self.prune(objects))
                except Exception as error:
                    logging.exception('Minis archive failed uid=%s sha=%s', uid, sha[:12])
                    with self.db() as connection:
                        connection.execute('UPDATE jobs SET status=?,attempts=attempts+1,retry_at=?,last_error=? WHERE uid=? AND sha=?', ('retry', int(time.time()) + min(3600, 30 * 2 ** min(job['attempts'], 7)), str(error)[:500], uid, sha))
            local_deleted = self.prune_local(objects)
            result = {'uploaded_verified': completed, 'deleted_after_nas_proof': deleted, 'local_archived_to_nas': local_deleted, 'cos_bytes': sum(objects.values())}
            temporary = self.root / 'last-run.pending.json'
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            temporary.replace(self.root / 'last-run.json')
            return result


def main():
    from minis_store import SnapshotStore
    manager = ArchiveManager(SnapshotStore(Path('/opt/notes-site/minis-store')), policy())
    print(json.dumps(manager.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
