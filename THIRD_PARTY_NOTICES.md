# Third-Party Notices

Much of snapclass's core behavior, along with portions of its implementation
and test suite, is adapted from the wonderful
[`datafiles`](https://github.com/jacebrowning/datafiles) project by
[Jace Browning](https://github.com/jacebrowning).

`datafiles` is licensed under the MIT License.

The TERSE formatter includes code adapted from
[`terse-py`](https://github.com/RudsonCarvalho/terse-py), the Python reference
implementation for [`TERSE`](https://github.com/RudsonCarvalho/terse-format).

The `terse-py` README declares the Python implementation MIT licensed. The
TERSE specification is referenced for compatibility, not copied into snapclass;
the `terse-format` repository describes the specification as CC BY 4.0 and the
implementations as MIT licensed.

The optional `yaml-fast` extra uses
[`tree-sitter`](https://github.com/tree-sitter/py-tree-sitter),
[`tree-sitter-yaml`](https://github.com/tree-sitter-grammars/tree-sitter-yaml),
and [`ruamel.yaml.clib`](https://pypi.org/project/ruamel.yaml.clib/) as runtime
dependencies. They are MIT-licensed projects. Snapclass imports their public
APIs; no source or generated parser code from these projects is copied or
vendored into snapclass.

## datafiles

The MIT License (MIT)

Copyright (c) 2018, Jace Browning

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Optional fast YAML dependencies

- tree-sitter / py-tree-sitter: MIT License
  - https://github.com/tree-sitter/py-tree-sitter
- tree-sitter-yaml: MIT License
  - https://github.com/tree-sitter-grammars/tree-sitter-yaml
- ruamel.yaml.clib: MIT License
  - https://pypi.org/project/ruamel.yaml.clib/

These projects are installed as optional runtime dependencies and distribute
their license files with their packages. No implementation source from them is
included in the snapclass distribution.

## terse-py / TERSE

Project: https://github.com/RudsonCarvalho/terse-py

Specification: https://github.com/RudsonCarvalho/terse-format

Upstream license declaration: MIT for implementations. The TERSE specification
is referenced for compatibility and is described upstream as CC BY 4.0; no
TERSE specification prose is copied into snapclass.

MIT License text for the adapted implementation:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
