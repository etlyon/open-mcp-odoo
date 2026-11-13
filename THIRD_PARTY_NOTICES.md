# Third-party notices

This project is licensed under AGPL-3.0-or-later (see LICENSE). It depends on the
libraries below, each under its own licence; none of their code is copied into
this repository. Their licence texts ship with each installed package.

Not affiliated with, endorsed by or sponsored by Odoo S.A., Nextcloud GmbH,
Infomaniak Network SA, Anthropic, OpenAI or Mistral AI. Odoo, Nextcloud, kSuite
and the assistants' names are trademarks of their owners and are used only to
say what this software connects to.

## Where the kSuite connector's knowledge of Infomaniak comes from

The kSuite tools call Infomaniak's APIs directly, in Python. Which endpoints they call, and what each
request and answer means (mail folders, threads, messages, attachments, drafts and sending; calendars
and events), was learned from Infomaniak's own MCP servers, published by Infomaniak under the MIT licence,
and from Infomaniak's public API documentation (https://developer.infomaniak.com). No file of these
packages is copied into this repository or shipped with it, and neither is a dependency.

| Package | Version read | Licence | Source |
|---|---|---|---|
| @infomaniak/mcp-server-mail | 2.1.0 | MIT (declared in its package.json and README) | https://www.npmjs.com/package/@infomaniak/mcp-server-mail · https://github.com/Infomaniak/mcp-server-mail |
| @infomaniak/mcp-server-calendar | 1.3.1 | MIT (declared in its package.json and README) | https://www.npmjs.com/package/@infomaniak/mcp-server-calendar · https://github.com/infomaniak/mcp-server-calendar |

Both packages name their author as Infomaniak (https://infomaniak.com) and print no copyright line. Where
this project follows their request shapes (for example the fields of a draft or of an event), it does so
under that licence:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction, including
> without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
> following conditions: The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
> KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
> PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
> CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF
> OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Python libraries (as pinned in requirements.lock)

| Library | Version | Licence | Source |
|---|---|---|---|
| aiofile | 3.12.3 | Apache-2.0 | https://github.com/mosquito/aiofile |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.14.2 | MIT | https://github.com/agronholm/anyio |
| attrs | 26.1.0 | MIT | https://tidelift.com/subscription/pkg/pypi-attrs?utm_source=pypi-attrs&utm_medium=pypi |
| Authlib | 1.8.0 | BSD License | https://github.com/authlib/authlib |
| beartype | 0.22.9 | MIT License |  |
| cachetools | 7.1.8 | MIT | https://github.com/tkem/cachetools/ |
| caio | 0.12.4 | Apache-2.0 | https://github.com/mosquito/caio/ |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | https://github.com/certifi/python-certifi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| click | 8.4.2 | BSD-3-Clause | https://github.com/pallets/click/ |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| cyclopts | 4.25.2 | Apache-2.0 | https://github.com/BrianPugh/cyclopts |
| defusedxml | 0.7.1 | Python Software Foundation License | https://github.com/tiran/defusedxml |
| dnspython | 2.8.0 | ISC License (ISCL) | https://www.dnspython.org |
| docstring_parser | 0.18.0 | MIT License | https://github.com/rr-/docstring_parser |
| email-validator | 2.3.0 | The Unlicense (Unlicense) | https://github.com/JoshData/python-email-validator |
| et_xmlfile | 2.0.0 | MIT License | https://foss.heptapod.net/openpyxl/et_xmlfile |
| exceptiongroup | 1.3.1 | MIT License | https://github.com/agronholm/exceptiongroup |
| fastmcp | 4.1.6 | Apache-2.0 | https://gofastmcp.com |
| fastmcp-slim | 4.1.6 | Apache-2.0 | https://gofastmcp.com |
| griffelib | 2.3.0 | ISC |  |
| h11 | 0.16.0 | MIT License | https://github.com/python-hyper/h11 |
| httpcore | 1.0.9 | BSD-3-Clause | https://www.encode.io/httpcore/ |
| httpcore2 | 2.12.0 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| httpx | 0.28.1 | BSD License | https://github.com/encode/httpx |
| httpx2 | 2.12.0 | BSD-3-Clause | https://github.com/pydantic/httpx2 |
| idna | 3.18 | BSD-3-Clause | https://github.com/kjd/idna |
| jaraco.classes | 3.4.0 | MIT License | https://github.com/jaraco/jaraco.classes |
| jaraco.context | 6.1.2 | MIT | https://github.com/jaraco/jaraco.context |
| jaraco.functools | 4.6.0 | MIT | https://github.com/jaraco/jaraco.functools |
| jeepney | 0.9.0 | MIT | https://gitlab.com/takluyver/jeepney |
| joserfc | 1.7.5 | BSD License | https://github.com/authlib/joserfc |
| jsonref | 1.1.0 | MIT | https://github.com/gazpachoking/jsonref |
| jsonschema | 4.26.0 | MIT | https://github.com/python-jsonschema/jsonschema |
| jsonschema-path | 0.5.0 | Apache Software License | https://github.com/p1c2u/jsonschema-path |
| jsonschema-specifications | 2025.9.1 | MIT | https://github.com/python-jsonschema/jsonschema-specifications |
| keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| lxml | 6.1.1 | BSD-3-Clause | https://github.com/lxml/lxml |
| markdown-it-py | 4.2.0 | MIT License | https://github.com/executablebooks/markdown-it-py |
| mcp | 2.3.0 | MIT License | https://modelcontextprotocol.io |
| mcp-types | 2.3.0 | MIT License | https://modelcontextprotocol.io |
| mdurl | 0.1.2 | MIT License | https://github.com/executablebooks/mdurl |
| more-itertools | 11.1.0 | MIT | https://github.com/more-itertools/more-itertools |
| openapi-pydantic | 0.6.0 | MIT License | https://github.com/mike-oakley/openapi-pydantic |
| openpyxl | 3.1.5 | MIT License | https://foss.heptapod.net/openpyxl/openpyxl |
| opentelemetry-api | 1.44.0 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pathable | 0.6.0 | Apache Software License | https://github.com/p1c2u/pathable |
| pillow | 12.3.0 | MIT-CMU | https://tidelift.com/subscription/pkg/pypi-pillow?utm_source=pypi-pillow&utm_medium=pypi |
| platformdirs | 4.11.8 | MIT | https://github.com/tox-dev/platformdirs |
| py-key-value-aio | 0.4.5 | Apache-2.0 |  |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.15.0 | MIT | https://github.com/pydantic/pydantic-settings |
| pydantic_core | 2.46.4 | MIT | https://github.com/pydantic |
| Pygments | 2.20.0 | BSD-2-Clause | https://pygments.org |
| PyJWT | 2.13.0 | MIT | https://github.com/jpadilla/pyjwt |
| pypdf | 6.18.0 | BSD-3-Clause | https://github.com/py-pdf/pypdf |
| pyperclip | 1.11.0 | BSD License | https://github.com/asweigart/pyperclip |
| python-docx | 1.2.0 | MIT License | https://github.com/python-openxml/python-docx |
| python-dotenv | 1.2.2 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| python-pptx | 1.0.2 | MIT License | https://github.com/scanny/python-pptx |
| PyYAML | 6.0.3 | MIT License | http://lists.sourceforge.net/lists/listinfo/yaml-core |
| referencing | 0.37.0 | MIT | https://github.com/python-jsonschema/referencing |
| rich | 15.0.0 | MIT License | https://github.com/Textualize/rich |
| rich-rst | 2.1.0 | MIT | https://github.com/wasi-master/rich-rst |
| rpds-py | 2026.6.3 | MIT | https://github.com/crate-py/rpds |
| SecretStorage | 3.5.0 | BSD-3-Clause | https://github.com/mitya57/secretstorage |
| sse-starlette | 3.4.8 | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| starlette | 1.6.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| truststore | 0.10.4 | MIT | https://github.com/sethmlarson/truststore |
| typing-inspection | 0.4.3 | MIT | https://github.com/pydantic/typing-inspection |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| uncalled-for | 0.4.0 | MIT License | https://github.com/chrisguidry/uncalled-for |
| uvicorn | 0.52.1 | BSD-3-Clause | https://uvicorn.dev/ |
| watchfiles | 1.2.0 | MIT License | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.1 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| xlsxwriter | 3.2.9 | BSD License | https://github.com/jmcnamara/XlsxWriter |
