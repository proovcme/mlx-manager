import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import catalog
import config
import discovery_paths as discovery

class DiscoveryPathTests(unittest.TestCase):
    def test_hf_environment_precedence_and_xdg(self):
        for env,expected in [({'XDG_CACHE_HOME':'/cache'},'/cache/huggingface/hub'),({'HF_HOME':'/hf','XDG_CACHE_HOME':'/cache'},'/hf/hub'),({'HF_HOME':'/hf','HF_HUB_CACHE':'/custom'},'/custom'),({'HUGGINGFACE_HUB_CACHE':'/legacy'},'/legacy')]:
            self.assertEqual(config.hub_cache(env),Path(expected))

    def test_configured_roots_accept_environment_json_and_validate(self):
        with patch.dict(os.environ,{'MLX_MANAGER_MODEL_ROOTS':'["~/models with spaces", "/data/models"]'}):
            self.assertEqual(config.configured_model_roots(),[Path.home()/'models with spaces',Path('/data/models')])
        for value in ['not json','"/models"','[1]']:
            with patch.dict(os.environ,{'MLX_MANAGER_MODEL_ROOTS':value}),self.assertRaises(ValueError):config.configured_model_roots()

    def test_omlx_bootstrap_settings_refresh_and_alias_deduplication(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);base=home/'custom app';base.mkdir();models=home/'first models';models.mkdir()
            bootstrap=home/'Library/Application Support/oMLX/base-path';bootstrap.parent.mkdir(parents=True);bootstrap.write_text(str(base))
            settings=base/'settings.json';settings.write_text(json.dumps({'model':{'model_dirs':[str(models)]}}))
            alias=home/'alias';alias.symlink_to(models,target_is_directory=True)
            with patch.object(Path,'home',return_value=home),patch.dict(os.environ,{},clear=True),patch.object(config,'MODEL_ROOTS',[alias]),patch.object(config,'EXTERNAL_MODEL',home/'absent'),patch.object(discovery,'launch_services',return_value=[]),patch.object(discovery,'running_services',return_value=[]):
                self.assertEqual(discovery.model_roots(),[models.resolve()])
                second=home/'second';second.mkdir();settings.write_text(json.dumps({'model':{'model_dirs':[str(second)]}}))
                self.assertEqual(discovery.model_roots(),[models.resolve(),second.resolve()])

    def test_launchers_and_cli_keep_spaces_and_ignore_unrelated_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);agents=home/'Library/LaunchAgents';agents.mkdir(parents=True)
            directories=[home/'models one',home/'models two'];args=['/tools/omlx','serve','--model-dir',','.join(map(str,directories))]
            (agents/'fixture.plist').write_bytes(plistlib.dumps({'ProgramArguments':args}))
            (agents/'broken.plist').write_text('<?xml malformed')
            (agents/'other.plist').write_bytes(plistlib.dumps({'ProgramArguments':['/tools/other','--model-dir','/not-models']}))
            with patch.object(Path,'home',return_value=home),patch.object(config,'EXTERNAL_PLIST',home/'missing.plist'):
                services=list(discovery.launch_services());self.assertEqual(len(services),1);self.assertEqual(discovery.service_paths(services[0]),directories)
            self.assertEqual(discovery.service_paths({'args':['python','-m','mlx_lm.server','--model='+str(directories[0])],'env':{},'cwd':None}),[directories[0]])
            self.assertFalse(discovery.is_model_runtime(['other','--model','omlx']))
            self.assertFalse(discovery.is_model_runtime(['zsh','-lc','python','-m','mlx_lm.server']))

    def test_cli_and_environment_override_omlx_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);(base/'settings.json').write_text(json.dumps({'model':{'model_dirs':['/settings-models']}}))
            service={'args':['omlx','serve','--base-path',str(base)],'env':{'OMLX_MODEL_DIR':'/env-models'},'cwd':None}
            self.assertEqual(discovery.service_paths(service),[Path('/env-models')])
            service['args']+=['--model-dir=/cli-models'];self.assertEqual(discovery.service_paths(service),[Path('/cli-models')])

    def test_arbitrary_hf_cache_name_keeps_repository_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'custom-cache';repo=root/'models--example--model';snapshot=repo/'snapshots/revision';snapshot.mkdir(parents=True)
            (repo/'refs').mkdir();(repo/'refs/main').write_text('revision')
            (snapshot/'config.json').write_text('{"model_type":"qwen3"}');(snapshot/'tokenizer_config.json').write_text('{}');(snapshot/'model.safetensors').write_bytes(b'fixture')
            with patch.object(catalog,'roots',return_value=[root]),patch.object(catalog,'runtimes',return_value={'mlx':{'id':'mlx'}}),patch.object(config,'image_snapshot',return_value=None):
                models=catalog.discover()['models'];self.assertEqual(len(models),1);self.assertEqual(models[0]['name'],'example/model');self.assertEqual(models[0]['path'],str(snapshot.resolve()))

if __name__=='__main__':unittest.main()
