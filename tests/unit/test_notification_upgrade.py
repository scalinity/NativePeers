from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import unittest

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.notification_policy import apply
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.routing import Router

# Exactly as stored in the live journal: a newline (not a space) precedes message_cutoff.
LEGACY=('CREATE TABLE notification_policies (peer_key TEXT NOT NULL,incarnation_id TEXT NOT NULL,state TEXT NOT NULL,\n '
        'message_cutoff INTEGER NOT NULL,route_cutoff INTEGER NOT NULL,closed_at REAL,opened_at REAL,PRIMARY KEY(peer_key,incarnation_id))')
CANONICAL=[('peer_key',1),('state',0),('message_cutoff',0),('route_cutoff',0),('closed_at',0),('opened_at',0),('incarnation_id',0)]


class NotificationUpgradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'state'
        Journal(self.root).close()
        self.database=self.root/'journal.sqlite3'

    def legacy(self,rows=(),sql=LEGACY):
        """A version-1 journal whose notification table has the obsolete composite-key shape."""
        db=sqlite3.connect(self.database,isolation_level=None)
        db.execute('DROP TABLE notification_policies');db.execute(sql)
        db.execute("INSERT INTO events(subject_id,kind,payload_json,at) VALUES('sentinel','kept','{}',1)")
        for row in rows:db.execute('INSERT INTO notification_policies VALUES(?,?,?,?,?,?,?)',row)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1);db.close()

    def state(self):
        db=sqlite3.connect(self.database)
        try:return db.execute("SELECT sql FROM sqlite_master WHERE name='notification_policies'").fetchone()[0],db.execute('SELECT * FROM notification_policies ORDER BY 1,2').fetchall()
        finally:db.close()

    def shape(self,db):return [(r[1],r[5]) for r in db.execute('PRAGMA table_info(notification_policies)')]

    def peer(self,registry,name,native):
        return registry.register('codex',self.root/'home',native,name,identity(os.getpid()).json(),str(self.root),{'kind':'fake'},owner_kind='test')

    async def test_empty_obsolete_table_is_upgraded_and_policy_semantics_work(self):
        self.legacy()
        journal=Journal(self.root);self.addCleanup(journal.close)
        self.assertEqual(self.shape(journal.db),CANONICAL)
        self.assertEqual(journal.db.execute("SELECT count(*) FROM events WHERE subject_id='sentinel' AND kind='kept'").fetchone()[0],1)
        self.assertEqual(journal.db.execute("SELECT count(*) FROM sqlite_master WHERE tbl_name='notification_policies' AND type='trigger'").fetchone()[0],0)
        registry=Registry(journal);a=self.peer(registry,'a','00000000-0000-4000-8000-000000000001');b=self.peer(registry,'b','00000000-0000-4000-8000-000000000002')
        journal.db.execute("UPDATE peer_instances SET status='live'")
        cb=Caller('peer',b['handle'],b['incarnation_id'])
        closed=await apply(journal,registry,cb,'close')
        self.assertEqual((closed['state'],closed['written_by_incarnation']),('QUIET',b['incarnation_id']))
        self.assertEqual(await apply(journal,registry,cb,'close'),closed)
        resumed=self.peer(registry,'b','00000000-0000-4000-8000-000000000002')
        journal.db.execute("UPDATE peer_instances SET status='live' WHERE incarnation_id=?",(resumed['incarnation_id'],))
        self.assertEqual(resumed['handle'],b['handle']);self.assertNotEqual(resumed['incarnation_id'],b['incarnation_id'])
        current=Caller('peer',resumed['handle'],resumed['incarnation_id'])
        self.assertEqual(await apply(journal,registry,current,'status'),closed)
        opened=await apply(journal,registry,current,'open')
        self.assertEqual((opened['state'],opened['message_cutoff'],opened['written_by_incarnation']),('OPEN',closed['message_cutoff'],resumed['incarnation_id']))
        again=await apply(journal,registry,current,'close')
        self.assertEqual((again['state'],again['written_by_incarnation']),('QUIET',resumed['incarnation_id']))
        self.assertEqual(journal.db.execute('SELECT count(*) FROM notification_policies').fetchone()[0],1)

    def test_repeated_and_concurrent_startup_is_idempotent(self):
        self.legacy();failures=[]
        def start():
            # SQLite connections stay in the thread that created them.
            try:Journal(self.root).close()
            except BaseException as error:failures.append(error)
        threads=[threading.Thread(target=start) for _ in range(6)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(failures,[])
        sql,rows=self.state();self.assertEqual(rows,[]);self.assertIn('peer_key TEXT PRIMARY KEY',sql)
        Journal(self.root).close()
        self.assertEqual(self.state(),(sql,[]))

    def test_fresh_canonical_journal_reopens_unchanged(self):
        before=self.state();self.assertIn('peer_key TEXT PRIMARY KEY',before[0])
        for _ in range(2):
            journal=Journal(self.root);self.assertEqual(self.shape(journal.db),CANONICAL);journal.close()
            self.assertEqual(self.state(),before)

    def test_legacy_ddl_accepts_flattened_whitespace_variant(self):
        self.legacy(sql=LEGACY.replace(',\n ',', '))
        journal=Journal(self.root);self.addCleanup(journal.close);self.assertEqual(self.shape(journal.db),CANONICAL)

    def test_nonempty_obsolete_policy_is_refused_and_preserved(self):
        self.legacy([('p','i1','QUIET',3,4,5.0,None)]);before=self.state()
        with self.assertRaises(PeerError) as caught:Journal(self.root)
        self.assertEqual(caught.exception.code,'CAPABILITY_MISMATCH')
        self.assertEqual(self.state(),before);self.assertEqual(before[1],[('p','i1','QUIET',3,4,5.0,None)])

    def test_unknown_shape_is_refused_and_preserved(self):
        self.legacy(sql='CREATE TABLE notification_policies (peer_key TEXT PRIMARY KEY,extra TEXT)');before=self.state()
        with self.assertRaises(PeerError) as caught:Journal(self.root)
        self.assertEqual(caught.exception.code,'CAPABILITY_MISMATCH');self.assertEqual(self.state(),before)

    def test_read_only_never_migrates_and_refuses_obsolete_shape(self):
        self.legacy();before=self.state()
        self.root.chmod(0o700)
        snapshot={path.name:path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        with self.assertRaises(PeerError) as caught:Journal(self.root,read_only=True)
        self.assertEqual(caught.exception.code,'CAPABILITY_MISMATCH');self.assertIn('notification',caught.exception.message)
        self.assertEqual(self.state(),before)
        self.assertEqual({path.name:path.read_bytes() for path in self.root.iterdir() if path.is_file()},snapshot)
        Journal(self.root).close()
        reader=Journal(self.root,read_only=True);self.addCleanup(reader.close)
        self.assertEqual(self.shape(reader.db),CANONICAL)


if __name__=='__main__':unittest.main()
