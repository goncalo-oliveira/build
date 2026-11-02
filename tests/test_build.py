import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(os.environ.get('BUILD_UNDER_TEST', str(Path(__file__).resolve().parents[1] / 'build')))

class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='build-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls'
        for command in ('docker', 'dotnet'):
            mock = self.bin / command
            mock.write_text('''#!/bin/bash
if [[ "$1" == version ]]; then
    printf 'linux/arm64\\n'
else
    printf '%s\\0' "${0##*/}" "$@" >> "$CALL_LOG"
    printf '\\n' >> "$CALL_LOG"
fi
''')
            mock.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:' + os.environ.get('PATH', '/usr/bin:/bin'), CALL_LOG=str(self.log))
        (self.root / 'application.csproj').write_text('<Project><PropertyGroup><Version>1.2.3</Version><OutputType>Exe</OutputType></PropertyGroup></Project>')
        (self.root / 'Dockerfile').write_text('FROM scratch\n')

    def run_build(self, *args, error=None, env=None):
        result = subprocess.run(['/bin/bash', str(SCRIPT), *args], cwd=self.root,
                                env=env or self.env, text=True, capture_output=True)
        if error is None:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(error, result.stderr)
            self.assertFalse(self.log.exists(), 'Invalid input executed a build')
        return result

    def manifest(self, kind, **extra):
        value = {'kind': kind, 'project': 'application.csproj'}
        if kind == 'docker':
            value.update(image='example/app', dockerfile='Dockerfile')
        else:
            value['output'] = 'artifacts with spaces'
        value.update(extra)
        (self.root / 'build.json').write_text(json.dumps({'init': value}))

    def calls(self):
        return [line.split('\0')[:-1] for line in self.log.read_text().splitlines()]

    def test_management_without_build_dependencies(self):
        env = dict(self.env, PATH='/usr/bin:/bin')
        self.assertIn('docker', self.run_build('--list-kinds', env=env).stdout)
        self.assertEqual(self.run_build('--version', env=env).stdout.strip(), '0.5.0')
        self.assertIn('Initialization options:', self.run_build('--help', env=env).stdout)
        self.assertIn('Usage:', self.run_build(env=env).stdout)

    def test_init_each_kind(self):
        for kind in ('docker', 'dotnet', 'nuget'):
            with self.subTest(kind=kind):
                name = f'{kind}.json'
                self.run_build('--init', '--kind', kind, '-c', name)
                data = json.loads((self.root / name).read_text())
                self.assertEqual(list(data), [f'example-{kind}'])
                self.assertEqual(data[f'example-{kind}']['kind'], kind)

    def test_init_all_and_no_overwrite(self):
        self.run_build('--init', '--kind', 'nuget', '--kind', 'docker', '--kind', 'dotnet')
        original = (self.root / 'build.json').read_bytes()
        data = json.loads(original)
        self.assertEqual(list(data), ['example-nuget', 'example-docker', 'example-dotnet'])
        self.assertEqual(data['example-dotnet']['platforms'], ['linux/amd64', 'linux/arm64'])
        self.assertEqual(data['example-docker']['context'], '.')
        self.run_build('--init', '--kind', 'nuget', error='refusing to overwrite')
        self.assertEqual((self.root / 'build.json').read_bytes(), original)

    def test_init_relative_and_absolute_config(self):
        folder = self.root / 'config with spaces'
        folder.mkdir()
        for path in ('config with spaces/relative.json', str(folder / 'absolute.json')):
            self.run_build('--kind', 'nuget', '-c', path, '--init')
            self.assertTrue((self.root / path).exists())

    def test_init_dangling_symlink(self):
        destination = self.root / 'missing.json'
        (self.root / 'build.json').symlink_to(destination)
        self.run_build('--init', '--kind', 'docker', error='refusing to overwrite')
        self.assertFalse(destination.exists())

    def test_invalid_arguments(self):
        cases = [
            (['--init'], 'requires at least one --kind'),
            (['--init', '--kind'], '--kind requires a kind'),
            (['--init', '--kind', ''], '--kind requires a kind'),
            (['--init', '--kind', '--config'], '--kind requires a kind'),
            (['--init', '--kind', 'unknown'], "unsupported kind 'unknown'"),
            (['--init', '--kind', 'docker', '--kind', 'docker'], 'selected more than once'),
            (['init', '--kind', 'docker'], '--kind requires --init'),
            (['--list-kinds', '--kind', 'docker'], '--kind requires --init'),
            (['--init', '--list-kinds'], 'choose only one management action'),
            (['--init', '--init'], 'choose only one management action'),
            (['--version', '--config', 'x'], 'does not accept --config'),
            (['--list-kinds', '-c', 'x'], 'does not accept --config'),
            (['--list-kinds', 'init'], 'cannot be combined with a target'),
            (['init', '--init', '--kind', 'docker'], 'cannot be combined with a target'),
            (['init', 'other'], 'only one target'),
            (['--unknown'], 'unknown option'),
            (['--config'], 'requires a non-empty path'),
            (['--config', '--init'], 'requires a non-empty path'),
            (['init', '-c', 'x', '-c', 'y'], 'can only be used once'),
            (['init', '--build-secret'], 'requires a non-empty Buildx'),
            (['init', '--build-secret', '--push'], 'requires a non-empty Buildx'),
            (['init', '--push', '--load'], 'cannot be repeated or used together'),
            (['init', '--load', '--load'], 'cannot be repeated or used together'),
            (['init', '--no-cache', '--no-cache'], 'can only be used once'),
            (['--config', 'x'], 'a target is required'),
            (['--init', '--kind', 'docker', '-c', 'missing/file.json'], 'does not exist'),
        ]
        for option in ('--push', '--load', '--no-cache', '--build-secret'):
            flags = [option, 'id=test'] if option == '--build-secret' else [option]
            for action in (['--init', '--kind', 'docker'], ['--list-kinds'], ['--version']):
                cases.append((action + flags, 'cannot be combined'))
        for args, error in cases:
            with self.subTest(args=args):
                self.run_build(*args, error=error)
        self.assertFalse((self.root / 'build.json').exists())

    def test_init_named_target_docker_modes(self):
        self.manifest('docker')
        for flags in ([], ['--push'], ['--load'], ['--no-cache', '--build-secret', 'id=one,src=some path', '--build-secret', 'id=two,env=TOKEN']):
            with self.subTest(flags=flags):
                self.log.unlink(missing_ok=True)
                self.run_build('init', *flags)
                call, = self.calls()
                self.assertEqual(call[:3], ['docker', 'buildx', 'build'])
                platform = call[call.index('--platform') + 1]
                self.assertEqual(platform, 'linux/arm64' if '--load' in flags else 'linux/amd64,linux/arm64')
                tags = [call[i + 1] for i, arg in enumerate(call) if arg == '--tag']
                self.assertEqual(tags, ['example/app:1.2.3', 'example/app:1.2', 'example/app:1', 'example/app:latest'])
                self.assertEqual(Path(call[-1]).resolve(), self.root)
                for flag in flags:
                    self.assertIn(flag if flag != '--build-secret' else '--secret', call)

    def test_configured_secrets_use_environment_without_exposing_values(self):
        self.manifest('docker', secrets={'github_token': 'BUILD_TEST_TOKEN', 'other': 'BUILD_TEST_OTHER'})
        env = dict(self.env, BUILD_TEST_TOKEN='private token,with=punctuation', BUILD_TEST_OTHER='other-private-value')
        result = self.run_build('init', '--build-secret', 'id=extra,env=EXTRA_TOKEN', env=env)
        call, = self.calls()
        specs = [call[i + 1] for i, arg in enumerate(call) if arg == '--secret']
        self.assertEqual(specs, ['id=extra,env=EXTRA_TOKEN', 'id=github_token,env=BUILD_TEST_TOKEN', 'id=other,env=BUILD_TEST_OTHER'])
        for value in (env['BUILD_TEST_TOKEN'], env['BUILD_TEST_OTHER']):
            self.assertNotIn(value, result.stdout + result.stderr + self.log.read_text())

    def test_configured_secrets_require_nonempty_exported_variables(self):
        self.manifest('docker', secrets={'github_token': 'BUILD_TEST_TOKEN'})
        env = dict(self.env)
        env.pop('BUILD_TEST_TOKEN', None)
        for value in (None, ''):
            with self.subTest(value=value):
                if value is not None:
                    env['BUILD_TEST_TOKEN'] = value
                result = self.run_build('init', env=env, error="target 'init' requires environment variable 'BUILD_TEST_TOKEN' for secret 'github_token'")
                self.assertNotIn('Building init', result.stdout)

    def test_invalid_configured_secrets(self):
        invalid = [None, False, [], 'TOKEN', {'': 'TOKEN'}, {'bad,id': 'TOKEN'},
                   {'bad=id': 'TOKEN'}, {'bad\nname': 'TOKEN'}, {'id': ''}, {'id': None},
                   {'id': 123}, {'id': 'BAD-NAME'}, {'id': '1TOKEN'}, {'id': 'TOKEN\n'}]
        for secrets in invalid:
            with self.subTest(secrets=secrets):
                self.manifest('docker', secrets=secrets)
                self.run_build('init', error="invalid 'secrets'")

    def test_empty_configured_secrets(self):
        self.manifest('docker', secrets={})
        self.run_build('init')
        call, = self.calls()
        self.assertNotIn('--secret', call)

    def test_duplicate_secret_ids(self):
        self.manifest('docker', secrets={'github_token': 'BUILD_TEST_TOKEN'})
        env = dict(self.env, BUILD_TEST_TOKEN='private-value')
        for spec in ('id=github_token,env=OTHER', 'env=OTHER,id=github_token', 'type=env,ID=github_token', '"id=github_token",env=OTHER', 'src="unused",id=github_token'):
            with self.subTest(spec=spec):
                result = self.run_build('init', '--build-secret', spec, env=env,
                                        error="duplicate secret ID 'github_token'")
                self.assertNotIn('private-value', result.stdout + result.stderr)
        self.manifest('docker')
        self.run_build('init', '--build-secret', 'id=same,env=ONE', '--build-secret', 'id=same,env=TWO',
                       error="duplicate secret ID 'same'")

    def test_quoted_secret_source_does_not_create_false_duplicate(self):
        self.manifest('docker', secrets={'github_token': 'BUILD_TEST_TOKEN'})
        self.run_build('init', '--build-secret', 'id=other,"src=path,id=github_token"',
                       env=dict(self.env, BUILD_TEST_TOKEN='private-value'))

    def test_secrets_only_allowed_for_docker(self):
        for kind in ('dotnet', 'nuget'):
            with self.subTest(kind=kind):
                self.manifest(kind, secrets={})
                self.run_build('init', error='unsupported fields: secrets')

    def test_all_preflights_secrets_before_any_builds(self):
        targets = self.mixed_manifest()
        targets['b-image'] = dict(targets['b-image'], secrets={'github_token': 'BUILD_TEST_TOKEN'})
        (self.root / 'build.json').write_text(json.dumps(targets))
        env = dict(self.env)
        env.pop('BUILD_TEST_TOKEN', None)
        self.run_build('.', env=env, error="target 'b-image' requires environment variable 'BUILD_TEST_TOKEN'")
        self.run_build('.', '--build-secret', 'id=github_token,env=OTHER',
                       env=dict(env, BUILD_TEST_TOKEN='private-value'), error="duplicate secret ID 'github_token'")

    def test_all_configured_secrets_stay_with_owning_target(self):
        targets = self.mixed_manifest()
        targets['b-image'] = dict(targets['b-image'], secrets={'github_token': 'BUILD_TEST_TOKEN'})
        (self.root / 'build.json').write_text(json.dumps(targets))
        self.run_build('.', env=dict(self.env, BUILD_TEST_TOKEN='private-value'))
        calls = self.calls()
        for call in calls[:3]:
            self.assertNotIn('--secret', call)
        self.assertIn('id=github_token,env=BUILD_TEST_TOKEN', calls[3])

    def host_runtime(self):
        import platform
        system = {'Darwin': 'osx', 'Linux': 'linux'}[platform.system()]
        arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}[platform.machine()]
        return f'{system}-{arch}'

    def test_dotnet_platforms_and_host_default(self):
        for platforms, expected in ((['linux/amd64', 'linux/arm64', 'windows/amd64', 'windows/arm64', 'darwin/amd64', 'darwin/arm64'], ['linux-x64', 'linux-arm64', 'win-x64', 'win-arm64', 'osx-x64', 'osx-arm64']), ([], [self.host_runtime()]), (None, [self.host_runtime()])):
            with self.subTest(platforms=platforms):
                self.log.unlink(missing_ok=True)
                self.manifest('dotnet', **({} if platforms is None else {'platforms': platforms}))
                self.run_build('init')
                calls = self.calls()
                self.assertEqual([c[c.index('--runtime') + 1] for c in calls], expected)
                for call in calls:
                    self.assertEqual(call[:2], ['dotnet', 'publish'])
                    self.assertIn('--self-contained', call)
                    self.assertIn('artifacts with spaces/', call[-1])

    def test_nuget(self):
        self.manifest('nuget')
        self.run_build('-c', 'build.json', 'init')
        call, = self.calls()
        self.assertEqual(call[:2], ['dotnet', 'pack'])
        self.assertEqual(call[-1], str(self.root / 'artifacts with spaces'))

    def test_inapplicable_options(self):
        for kind in ('dotnet', 'nuget'):
            self.manifest(kind)
            for flags in (['--push'], ['--load'], ['--no-cache'], ['--build-secret', 'id=one']):
                with self.subTest(kind=kind, flags=flags):
                    self.run_build('init', *flags, error='only to docker targets')

    def test_unknown_fields(self):
        for kind, extra in (('dotnet', {'image': 'bad'}), ('nuget', {'platforms': []}), ('docker', {'output': 'bad'}), ('docker', {'imag': 'bad'})):
            with self.subTest(kind=kind, extra=extra):
                self.manifest(kind, **extra)
                self.run_build('init', error=f"unsupported fields: {next(iter(extra))}")

    def test_invalid_manifests(self):
        self.run_build('init', error='missing build.json')
        for document, error in (({}, 'no object entry'), ({'init': {}}, "requires a non-empty 'kind'"), ({'init': {'kind': 'unknown'}}, 'unsupported kind')):
            (self.root / 'build.json').write_text(json.dumps(document))
            self.run_build('init', error=error)
        self.manifest('dotnet', platforms='linux/amd64')
        self.run_build('init', error="invalid 'platforms'")


    def mixed_manifest(self):
        docker = {'kind': 'docker', 'project': 'application.csproj', 'image': 'example/app', 'dockerfile': 'Dockerfile'}
        dotnet = {'kind': 'dotnet', 'project': 'application.csproj', 'output': 'cli', 'platforms': ['linux/amd64']}
        nuget = {'kind': 'nuget', 'project': 'application.csproj', 'output': 'packages'}
        targets = {'z-library': nuget, 'a-image': docker, 'm-cli': dotnet, 'b-image': docker}
        (self.root / 'build.json').write_text(json.dumps(targets))
        return targets

    def test_all_configuration_order_and_docker_options(self):
        self.mixed_manifest()
        for flags in ([], ['--push'], ['--load'], ['--no-cache', '--build-secret', 'id=one,env=TOKEN']):
            with self.subTest(flags=flags):
                self.log.unlink(missing_ok=True)
                result = self.run_build('.', *flags)
                calls = self.calls()
                self.assertEqual([c[:2] for c in calls], [
                    ['dotnet', 'pack'], ['docker', 'buildx'], ['dotnet', 'publish'], ['docker', 'buildx']])
                self.assertIn('Built all 4 targets successfully.', result.stdout)
                for number, name in enumerate(['z-library', 'a-image', 'm-cli', 'b-image'], 1):
                    self.assertIn(f'Building target {name} ({number}/4)', result.stdout)
                for call in (calls[1], calls[3]):
                    for flag in flags:
                        self.assertIn('--secret' if flag == '--build-secret' else flag, call)
                for call in (calls[0], calls[2]):
                    self.assertFalse(set(call) & {'--push', '--load', '--no-cache', '--secret'})

    def test_all_custom_config(self):
        self.mixed_manifest()
        config = self.root / 'custom.json'
        (self.root / 'build.json').rename(config)
        self.run_build('.', '-c', str(config))
        self.assertEqual(len(self.calls()), 4)

    def test_all_stops_after_docker_failure(self):
        self.mixed_manifest()
        with (self.bin / 'docker').open('a') as mock:
            mock.write('exit 23\n')
        result = subprocess.run(['/bin/bash', str(SCRIPT), '.', '--push'], cwd=self.root,
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn('Build failed for target a-image (1/4 targets completed).', result.stderr)
        self.assertNotIn('successfully', result.stdout)

    def test_all_stops_inside_dotnet_platform_loop(self):
        self.manifest('dotnet', platforms=['linux/amd64', 'linux/arm64'])
        with (self.bin / 'dotnet').open('a') as mock:
            mock.write('exit 24\n')
        result = subprocess.run(['/bin/bash', str(SCRIPT), '.'], cwd=self.root,
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 24)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn('Build failed for target init (0/1 targets completed).', result.stderr)

    def test_all_validation_failure_stops(self):
        targets = self.mixed_manifest()
        targets['a-image']['unexpected'] = True
        (self.root / 'build.json').write_text(json.dumps(targets))
        result = subprocess.run(['/bin/bash', str(SCRIPT), '.'], cwd=self.root,
                                env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn('unsupported fields: unexpected', result.stderr)
        self.assertIn('(1/4 targets completed)', result.stderr)

    def test_all_rejects_empty_invalid_and_reserved_names(self):
        for targets, error in (({}, 'has no targets to build'), ([], 'must contain an object'),
                               ({'.': {'kind': 'nuget'}}, "target name '.' is reserved")):
            with self.subTest(targets=targets):
                (self.root / 'build.json').write_text(json.dumps(targets))
                self.run_build('.', error=error)

    def test_all_target_names_with_whitespace(self):
        self.manifest('nuget')
        target, = json.loads((self.root / 'build.json').read_text()).values()
        names = ['target with spaces', 'target\nwith newline', 'trailing newline\n']
        (self.root / 'build.json').write_text(json.dumps(dict.fromkeys(names, target)))
        self.run_build('.')
        self.assertEqual(len(self.calls()), 3)

if __name__ == '__main__':
    unittest.main(verbosity=2)
