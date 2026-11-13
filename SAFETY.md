# MCP Apps 自動送信検証：重複事故の暫定停止と再開条件 (2026-11-13)

## 安全状態
- 旧リソース `ui://chatgpt-event-probe/auto.html` と `show_auto_probe` は**使用停止**。現在のファイルは送信コードを含まない。
- 旧チャットを開かない。ホストに保存・キャッシュされた旧HTMLは、現行ソースの修正だけでは無効化を保証できない。トンネル停止も `ui/message` の直接送信を遮断する保証にはならない。
- `show_guarded_probe` は新規URIを使う別検証UI。**2026-11-13の送信先安全対策では、`MCP_AUTO_SEND_ENABLED=1` でも `claim_guarded_probe` が `target_unverified` を返し、送信権を発行しない**。サーバー検証可能な送信先認証とホストの原子的な送信先固定が成立するまでは解除しない。
- ユーザーによる追加送信テストの明示指示が出るまで、UIの表示・MCPサーバーの起動・トンネルの再開・環境変数による送信許可を行わない。

## コード上の安全設計
- `dispatch_store.py` はSQLiteの永続レジャーで、`BEGIN IMMEDIATE` と主キーによって競合要求を一度に限定する。
- `state/dispatch.sqlite3` は明示的に初期化済み。元の `/workspace` 永続マウントを使うこと。コンテナ内部だけの一時パスへ移動しない。
- `claim_once` は事前にDBがない、読み取り不可、送信権が使われた、許可フラグがない場合に fail-closed。
- この単発テストのグローバル上限は1。2〜3ターンの自動往復は専用のイベントID・状態遷移・上限・ユーザー承認を別途設計する。
- 発行済み claim は送信成否にかかわらず永久に再使用不可。ホストの `isError=false` はホスト受理を意味するだけで、ChatGPTの実際の応答や外部サーバーでの結果受領を保証しない。
- ホストタイムアウトや状態が不明の場合は `unknown` として記録し、自動再試行しない。
- ツール `claim_guarded_probe`/`report_guarded_probe` は `visibility=['app']`。ChatGPTホストでの `serverTools` capability は実際の新UI表示時に検証が必要。

## 未検証・制約
- キャッシュされた旧HTMLの再描画挙動と実効的な停止（旧チャットは開かない）。
- `ui/message` の送信後にAI回答をサーバー側へ回収する機構。
- ユーザー端末・画面バックグラウンド・セッション継続。
- DBレジャーは「再実行しない」運用（at-most-once attempt）を提供するが、配送成功のexactly-onceや応答到達を保証しない。
- iframe/ホストがサーバーツールを使えるかは実機未検証。別アプリやサーバー接続が同じ固定イベントのclaimを先に消費する可能性がある。公開サーバーには別途強固な認証と認可が必要。
- ChatGPT UI自体が古いUIをキャッシュしている場合はサーバー側で修正不能なことがある。

## 安全なコード検証コマンド（送信なし）
```sh
cd /workspace/chatgpt-event-widget-probe
.venv/bin/python -W error::ResourceWarning -m unittest -v test_dispatch_store.py
.venv/bin/python -m py_compile server.py dispatch_store.py test_dispatch_store.py
```

**新しい実機テストの承認は、技術的な送信先固定の検証を免除しない。** 実行環境が変更されても、`MCP_AUTO_SEND_ENABLED=1` は設定しない。設定されても現行の送信権APIは拒否する。これには旧HTMLをホストがキャッシュした場合の直接 `ui/message` 呼び出しを止める効力はないため、旧チャットは開かない。

## 2026-11-13 実機検証試行（ユーザーの明示的な再開指示後）

- 送信許可フラグを1回の実機試行に限って有効化し、MCPサーバーと専用トンネルの接続成功を確認。
- このチャットのプラグインツール一覧には `show_guarded_probe` が反映されていなかった。そのため `show_event_probe` を新リソース `guarded-v2.html` へ一時的に割り当て、**1回だけ**呼び出した。ツール結果は「UIを埋め込んだ」と返した。
- その後、サーバーの永続レジャーは終始 `not_claimed`。ChatGPT実機からの送信は未確認。ツール呼び出し成功やUI埋め込み表示と、実際のUI初期化・送信権取得を混同しない。
- 旧 `show_auto_probe` は呼び出していない。
- 試行後、サーバーとトンネルを停止し、`show_event_probe` のリソースURIを元の `manual.html` に戻した。
- ローカルSDKで `show_guarded_probe` や `guarded_probe_status` を列挙でき、`guarded_probe_status` は `not_claimed` を返した。
- `test_dispatch_store.py` の12件が成功。
- 新規 `test-guarded-ui-offline.cjs`（ネットワーク・ChatGPT接続なしの模擬MCP Appsホスト）では、UIソースをそのまま実行し、再生成・並列生成・応答不明・機能不足・teardownの5ケースが成功。送信数は期待どおり0または1。
- 次の実機検証は、プラグインの新ツール定義がChatGPTへ反映されたのを確認してから、`show_guarded_probe` を直接1回呼び出す。現状の古いプラグイン定義では結果を断定できない。
- キャッシュされたUIがすでに表示されている可能性があるため、誤って手動送信ボタンを押さない。
