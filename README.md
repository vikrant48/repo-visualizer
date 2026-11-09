# repo-visualizer

A Claude Code plugin that analyzes your codebase and generates an interactive HTML page showing the full journey of a request: user → gateway → middleware → services → cache → database/queue → response, plus how all services connect.

## Install

```
/plugin marketplace add vikrant48/repo-visualizer
/plugin install repo-visualizer@repo-visualizer
```

## Usage

```
/repo-visualizer:visualize
```

## License

MIT
