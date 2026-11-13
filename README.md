# ChatGPT MCP Bridge — experimental

ChatGPTの**通常チャット**とMCP Appsを経由した外部システムの安全な双方向連携を研究する実験プロジェクトです。OpenAI APIによる別チャットへの置き換えは目的ではありません。

## 現在の検証状況（2026-11-13）

- MCP Appsの `ui/message` によるChatGPT通常チャットへの送信は実機で観測済み。
- 旧自動送信UIは重複送信事故のため停止済み。**旧 `show_auto_probe` を実行しないでください。**
- 旧重複対策UI `show_guarded_probe` は、送信先チャットを認証できないため、環境変数を有効にしてもサーバーが送信権を拒否します。
- 重複防止は **at-most-once attempt** です。exactly-once deliveryや応答回収は保証しません。
- 初回のPython単体テスト12件・模擬ホストテスト5件に成功。以後の追加テストはCIで継続検証しています。新しいThread EntrypointのiPhone実機動作は未確認です。
- ChatGPTの応答をMCPサーバーが自動回収する仕組みは未完成です。

## 主なファイル

| ファイル | 用途 |
| --- | --- |
| `server.py` | MCPサーバーとUIリソースの定義 |
| `guarded-probe.html` | 永続送信権により保護した実験UI |
| `dispatch_store.py` | SQLiteによる重複送信防止 |
| `auto-probe.html` | 旧自動送信UIの停止画面 |
| `test_dispatch_store.py` | 送信権管理のPythonテスト |
| `test-guarded-ui-offline.cjs` | ChatGPT非接続のUI模擬ホストテスト |
| `SAFETY.md` | 検証履歴と安全上の制約 |
| `thread_entrypoint_server.py` | チャット単位の表示専用UIサーバー（送信機能なし） |
| `docs/thread-target-routing-research.md` | 宛先固定の仕様調査、代替構成、安全ゲート |

## 安全上の注意

**初期状態では自動送信を許可しないでください。** 特に、送信履歴DBの削除・再初期化は重複送信制御を無効化する危険があります。送信結果が不明な場合も自動再試行しません。

`state/`、`.secrets/`、`tunnel-profiles/`、トンネルログ・バイナリ等は公開対象外です。`.gitignore` は補助策であり、公開前にはステージング対象ファイルを個別に確認し、シークレットスキャンを実施してください。

このコードは研究用の試作であり、認証・認可・セッション管理・外部からのアクセス制御を備えた本番サービスではありません。

## ネットワーク非接続のテスト

```sh
.venv/bin/python -W error::ResourceWarning -m unittest -v test_dispatch_store.py
node test-guarded-ui-offline.cjs
```

詳細は [`SAFETY.md`](SAFETY.md) と [`docs/architecture-and-validation.md`](docs/architecture-and-validation.md) を参照してください。

CIではPython 3.14 / Node.js 24を使用し、ネットワークに接続する実機送信を一切行いません。開発環境の作成例:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest -v test_dispatch_store.py
node test-guarded-ui-offline.cjs
```
