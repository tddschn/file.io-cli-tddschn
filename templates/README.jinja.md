# file.io-cli-tddschn

Fork of `file.io-cli` with maintenance and enhancements.

    $ pip install file.io-cli-tddschn

Command-line tool to upload files to https://file.io

  [file.io]: https://www.file.io

- [file.io-cli-tddschn](#fileio-cli-tddschn)
    - [Synopsis](#synopsis)
    - [Examples](#examples)
  - [Installation](#installation)
    - [pipx](#pipx)
    - [pip](#pip)
  - [Develop](#develop)
    - [Changelog](#changelog)
      - [1.0.5](#105)
      - [v1.0.4](#v104)
      - [v1.0.3](#v103)
      - [v1.0.2](#v102)
      - [v1.0.1](#v101)
      - [v1.0.0](#v100)


### Synopsis

{{ prompt_commands_and_usages }}

### Examples

Upload a file and copy the link:

```
$ file.io hello.txt -c
[============================================================] 100% (15 bytes / 15 bytes)
https://file.io/pgiPc2 (copied to clipboard)
$ cat https://file.io/pgiPc2
Hello, File.io!
```

Upload a compressed archiveCompress a file/directory and upload it (streaming):

```
$ file.io -zt AllMyFiles/
/ (55MB)
https://file.io/sf2La
```

Upload from stdin:

```
$ find .. -iname \*.py | file.io -n file-list.txt
/ (312KB)
https://file.io/uRglUT
```

Upload a file 3 times concurrently:

```
$ file.io -N 3 file_io_cli_tddschn/cli.py

https://file.io/Vv7QtVfMVBr2
https://file.io/10Y2DgoXDJwQ
https://file.io/rCoWI2PN58cg
```

## Installation

### pipx

This is the recommended installation method.

```
$ pipx install file.io-cli-tddschn
```

### [pip](https://pypi.org/project/file.io-cli-tddschn/)

```
$ pip install file.io-cli-tddschn
```

## Develop

```
$ git clone https://github.com/tddschn/file.io-cli-tddschn.git
$ cd file.io-cli-tddschn
$ poetry install
```

### Changelog

#### 1.1.2

* Add -N, --num-threads option to upload files multiple times concurrently
* Fix the uploaded file name when using relative path like `.`

#### 1.0.5

* Add -v, --verbose option to print server response in JSON
* Use poetry for developing and packaging

#### v1.0.4

* Fix missing entrypoint in new setup script

#### v1.0.3

* Fix declared dependencies in setup script

#### v1.0.2

* Replaced `time.clock` (removed in python 3.8) with `time.perf_counter`
* Minimum Python version is 3.3

#### v1.0.1

* Add `-t, --tar` and `-z, --gzip` options
* Fix NameError when using `-c, --clip`
* Fix progress bar left incomplete

#### v1.0.0

* Initial version
