import asyncio,hashlib,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.cli import parser,dispatch
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import new_id,RESULT_PAGE
from nativepeers.process import identity
from nativepeers.registry import Registry


class ArtifactExportTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        peer=Registry(self.j).register('codex',self.root/'home',new_id(),'parent',identity(os.getpid()).json(),str(self.root),{'kind':'test'},owner_kind='test')
        self.caller=Caller('peer',peer['handle'],peer['incarnation_id']);self.service=Artifacts(self.j)
        self.raw=bytes(range(256))*(RESULT_PAGE//128+1);self.handle=self.service.put(self.raw,self.caller.key)
        self.path=self.root/'output.patch'

    def test_cli_binary_pages_digest_and_worker_delta_metadata(self):
        self.j.event(self.handle,'worker-delta-artifact',{'input_tree':'frozen-tree'})
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            value=asyncio.run(dispatch(parser().parse_args(['artifact-export',self.handle,'--output',str(self.path),'--json']),self.j))
        self.assertEqual(self.path.read_bytes(),self.raw);self.assertEqual(value['sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(value['worker_delta_base'],'frozen-tree');self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertFalse(list(self.root.glob('.npeer-export-*')))

    def test_existing_file_and_symlink_destinations_are_preserved(self):
        self.path.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):self.service.export(self.handle,self.path,self.caller)
        self.assertEqual(self.path.read_bytes(),b'keep');self.path.unlink()
        target=self.root/'target';target.write_bytes(b'keep');self.path.symlink_to(target)
        with self.assertRaises(FileExistsError):self.service.export(self.handle,self.path,self.caller)
        self.assertEqual(target.read_bytes(),b'keep');self.assertTrue(self.path.is_symlink())
        linked=self.root/'linked';linked.symlink_to(self.root,target_is_directory=True)
        value=self.service.export(self.handle,linked/'nested',self.caller)
        self.assertEqual(value['path'],str(self.root/'nested'));self.assertEqual((self.root/'nested').read_bytes(),self.raw);self.assertFalse(list(self.root.glob('.npeer-export-*')))

    def test_interrupted_or_corrupt_export_leaves_no_output(self):
        read=self.service.read;calls=0
        def interrupted(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('fixture interrupted read')
            return read(*args,**kwargs)
        with patch.object(self.service,'read',side_effect=interrupted):
            with self.assertRaises(OSError):self.service.export(self.handle,self.path,self.caller)
        self.assertFalse(self.path.exists());self.assertFalse(list(self.root.glob('.npeer-export-*')))
        (self.j.root/'artifacts'/self.handle).write_bytes(b'corrupt')
        with self.assertRaises(PeerError):self.service.export(self.handle,self.path,self.caller)
        self.assertFalse(self.path.exists())

    def test_reader_grants_and_worker_write_scope_apply_before_publication(self):
        private=self.service.put(b'private','other')
        with self.assertRaises(PeerError):self.service.export(private,self.path,self.caller)
        self.assertFalse(self.path.exists())
        analysis=Caller('peer',self.caller.key,self.caller.incarnation,{'worker_id':'worker','profiles':['analysis']})
        with self.assertRaises(PeerError):self.service.export(self.handle,self.path,analysis)
        workspace=Caller('peer',self.caller.key,self.caller.incarnation,{'worker_id':'worker','profiles':['workspace']})
        with self.assertRaises(PeerError):self.service.export(self.handle,self.root.parent/'outside',workspace)
        self.service.export(self.handle,self.path,workspace);self.assertEqual(self.path.read_bytes(),self.raw)
