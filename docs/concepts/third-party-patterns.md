<!--
scope-owned: upstream pattern attributions
audience: all
source: hand
review-trigger: new borrow
-->

# Third-Party Patterns

This file fulfills the MIT license requirement to retain copyright notices
and license text when a work is used or adapted. It covers patterns adopted
by S.A.G.E. from external sources.

---

## caveman — JuliusBrussee

**Pattern name:** caveman  
**Author:** JuliusBrussee  
**Repository:** <https://github.com/JuliusBrussee/caveman>  
**License:** MIT

### What S.A.G.E. adopted

The "agent output discipline" patterns referenced in `agents/*.md` and
`claude-md/CLAUDE.md` §14. Specifically: the compressed inter-agent
communication style — dropping articles, filler, and pleasantries;
using fragments; keeping technical terms exact; leading with status and
commit SHA rather than prose recap. We ADAPTED these conventions rather
than copying verbatim; the original caveman DSL targets a different
runtime. S.A.G.E. uses the discipline as a behavioral constraint in
subagent prompts.

### Why we attribute

The MIT license requires that copyright and license notices be preserved
in all copies or substantial portions of the software. This file is that
notice. The caveman pattern informs every agent in `agents/*.md`; this
attribution travels with the repo.

### MIT License (verbatim)

```
MIT License

Copyright (c) JuliusBrussee

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
