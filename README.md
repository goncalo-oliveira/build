# build

A small Bash script for building Docker images, self-contained .NET executables, and NuGet packages from named targets in a `build.json` file.

## Setup

Put the executable `build` script in a directory on your `PATH`, or run it directly as `./build`.

Requirements:

- Bash.
- `jq` to create configurations and read targets.
- `xmllint` to read project metadata when building.
- Docker with Buildx for `docker` targets.
- The .NET SDK for `dotnet` and `nuget` targets.

## Quick start

List the available kinds, then generate a configuration with one or more examples:

```sh
build --list-kinds
build --init --kind docker --kind dotnet --kind nuget
```

Initialization creates one target per selected kind, named `example-docker`, `example-dotnet`, or `example-nuget`. It requires at least one kind and refuses to overwrite an existing file.

Edit the example names and settings in `build.json`, then build a target:

```sh
build application
build application --load
build application --push
build .                          # Build every configured target
```

`build .` builds every target in the selected configuration, sequentially and in configuration order. It stops on the first failure and prints a summary. It does not scan directories for projects. Use `build . --config path/to/build.json` to select another configuration.

Docker options apply only to Docker targets during a full build: `build . --push` pushes Docker images and builds .NET executables and NuGet packages normally.

The name `.` is reserved for building all targets. Other target names are unrestricted by management commands: `build init` builds a target named `init`.

## Configuration

The script reads `build.json` from the working directory. Use `--config` (or `-c`) to choose another file, including during initialization. Relative paths inside the configuration are resolved from that file's directory.

```json
{
  "application": {
    "kind": "docker",
    "project": "src/application/application.csproj",
    "image": "registry.example.com/organization/application",
    "dockerfile": "Dockerfile",
    "context": "."
  },
  "cli": {
    "kind": "dotnet",
    "project": "src/cli/cli.csproj",
    "output": "artifacts/cli",
    "platforms": ["linux/amd64", "linux/arm64"]
  },
  "library": {
    "kind": "nuget",
    "project": "src/library/library.csproj",
    "output": "artifacts/packages"
  }
}
```

| Kind | Required fields | Optional fields |
| --- | --- | --- |
| `docker` | `kind`, `project`, `image`, `dockerfile` | `context` (defaults to `.`), `secrets` (secret ID to environment variable mapping) |
| `dotnet` | `kind`, `project`, `output` | `platforms` (defaults to the host platform when omitted or empty) |
| `nuget` | `kind`, `project`, `output` | — |

Unknown fields and fields belonging to another kind are rejected. Every project must explicitly define a stable `<Version>` such as `1.2.3`; prerelease versions are rejected. Executable projects must also explicitly define `<OutputType>Exe</OutputType>`.

### Docker

```sh
build application                   # Multi-platform build; result stays in Buildx cache
build application --load            # Load an image for the local Docker daemon's platform
build application --push            # Push multi-platform images to the registry
build application --no-cache
build application --build-secret id=token,env=BUILD_TOKEN
```

Default and pushed builds target `linux/amd64` and `linux/arm64`. `--load` requires a Linux Docker daemon and targets its architecture. `--push` and `--load` are mutually exclusive. `--build-secret` may be repeated.

Define required secrets on a Docker target to build with just `build application`:

```json
{
  "application": {
    "kind": "docker",
    "project": "src/application/application.csproj",
    "image": "registry.example.com/organization/application",
    "dockerfile": "Dockerfile",
    "secrets": {
      "github_token": "GITHUB_TOKEN"
    }
  }
}
```

Each key is the secret ID used in the Dockerfile; each value names an exported environment variable. IDs may contain letters, digits, `.`, `_`, and `-`. Environment variable names must start with a letter or `_` and contain only letters, digits, and `_`. Omit `secrets` or use `{}` when none are required.

```sh
export GITHUB_TOKEN  # After setting it in your shell
build application
```

The script forwards this as `--secret id=github_token,env=GITHUB_TOKEN`. It stops before starting Docker if a required variable is unset or empty. For `build .`, all Docker targets' configured secrets are checked before any builds start. Store only variable names in `build.json`; secret values stay in the environment and are never printed or added to Docker command arguments.

`--build-secret` can supply additional Buildx secret specifications. Duplicate IDs across configured and command-line secrets, or repeated command-line IDs, are rejected rather than overridden. Configured secrets belong only to their target and are not supported on .NET or NuGet targets.

The project version supplies image tags: `1.2.3` produces `1.2.3`, `1.2`, `1`, and `latest`.

### .NET executables

```sh
build cli
```

Publishes a self-contained, single-file Release executable for each platform, under `<output>/<os>-<architecture>/`.

Supported platforms are `linux/amd64`, `linux/arm64`, `darwin/amd64`, `darwin/arm64`, `windows/amd64`, and `windows/arm64`. Automatic host detection supports Linux and macOS.

### NuGet packages

```sh
build library
```

Runs `dotnet pack` in Release mode and writes packages to the configured output directory.

## Other options

```sh
build cli --config path/to/build.json
build --init --kind nuget --config path/to/build.json
build --help
build --version
```

Docker options are rejected for individual targets of other kinds. `--list-kinds`, `--help`, and `--version` work without a configuration file or build dependencies.

## Tests

Run the CLI tests with Python 3 (3.8 or later), Bash, `jq`, and `xmllint` installed:

```sh
python3 tests/test_build.py
bash -n build
```

The tests use temporary configurations and stand-ins for Docker and .NET, so they do not build or push real artifacts.
