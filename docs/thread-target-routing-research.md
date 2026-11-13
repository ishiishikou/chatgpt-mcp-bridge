# 通常チャットの送信先固定 — 技術検証（Issue #4）

更新: 2026-11-13。状態: **送信先固定は未証明。実機自動送信は停止。**

## 実際に確認したこと

- 旧 `show_auto_probe` の `ui/message` は、通常のChatGPTチャットに無操作で届いた実績がある。しかし一度の表示で5件の重複があり、送信先の安全な固定も示していない。
- iPhoneでは、Chat AのMCP widgetが別のCodexレビュー用Chat Bにも表示されたスクリーンショットがある。DOMの同一インスタンス再利用か、別のiframe再生成か、ChatGPTホスト自身の表示バグかは未確定。
- 元の `dispatch_store.py` はイベントの同時取得上限を担保するが、`claim` の宛先チャットが正しいことは証明できない。
- 現在の `server.py` は事故防止のため、**環境変数を1にしても** `claim_guarded_probe` が `target_unverified` を返す。既存のSQLiteレジャーは初期化・変更しない。
- 新しい `thread_entrypoint_server.py` は1つの表示専用ツールと1つのHTMLリソースだけを登録する。旧送信機能、SQLite、外部キュー、HTTPへの接続を持たない。
- ローカルMCPの `tools/list` では `_meta["openai/ui"]["entrypoints"]=[{"type":"thread"}]` と `_meta.ui.resourceUri` を確認できた。ローカルで `tools/call` と `resources/read` に成功した。これはChatGPTのiPhone実機での表示成功ではない。

## 公式仕様との突き合わせ

1. MCP Appsの標準 `ui/initialize` はUIのホストコンテキスト、能力、`toolInfo`（ツール呼び出しID）を伝達する。**サーバーが検証できるChatGPT通常チャットIDを提供するという保証はない**。
2. OpenAI MCP拡張の **Thread Entrypoint** はチャット内のコンテンツタブであり、各チャットは異なるインスタンスを持つと定義される。iOSの対応も記載されている。ただし、これは「UIインスタンス分離」の約束であって、`ui/message` の原子的な宛先固定ではない。
3. 拡張 `ui/message` の対象は `target:"active"`（既定値）または `target:"new"`。**既存の特定チャットIDを指定する値は仕様にない**。既定は `send:true`。`target:"active",send:false` なら草稿作成として使えるが、送信されず、ユーザーによる内容・宛先確認が必要。
4. `openai/deepLink` はアプリ内部の相対URLであり、ChatGPTチャットIDの証明ではない。`hostContext` の任意フィールド、`toolInfo.id`、URL、WidgetインスタンスID、ツール入力、モデルが自己申告したチャット名・IDも「検証された宛先」にならない。
5. `ui/notifications/tool-result` はMCPツール結果であり、任意のAssistant回答回収の証拠ではない。
6. MCP Eventsの明示対象がWork/dotsであることは、通常チャットで過去に実証した前景MCP UI送信を否定しない。ただし、閉じた通常チャットへの無人起動は依然未検証。

公式出典:
- https://github.com/openai/mcp-extensions/blob/main/docs/spec.md （Thread Entrypoint、`openai/message` target）
- https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx （MCP Apps標準）
- https://developers.openai.com/plugins/build/chatgpt-ui
- https://developers.openai.com/plugins/build/mcp-events

## スレッド固定の検証に必要な証拠

**UI分離の検証（送信なし）**
1. ChatGPT上で新しい専用MCPプラグインに `show_thread_entrypoint_probe` が表示されること。
2. 通常チャットA内のThread Entrypointを開き、UIインスタンスID・生成時刻を記録。
3. 通常チャットBに移動して*新たなツール実行なし*で表示状態を観測。BのThread Entrypointを開く場合も、その操作を区別する。
4. Aに戻ってインスタンスID・再表示回数・teardownを比較。iPhone/Windowsを別々に記録。
5. 同一IDならビュー移送/再利用の可能性、異なるIDなら再生成の証拠。**いずれも自動メッセージの宛先固定を証明しない**。

**実機自動送信に進む追加条件**
- ChatGPTホスト自身が実行時に対象チャットを検証可能な形で認証し、その**同じ対象**に対して`ui/message`を原子的にルーティングする仕組みを提供し、テストで確認できること。
- OAuthでユーザー主体を確認できても、特定のチャットIDの認証とは別。アプリ内の秘密トークンを持っているだけでも、別チャットへ移動したビューの送信先を固定できない。
- 送信先が証明できない場合は、時刻、画面可視性、`window.parent`、URL、モデルの自己申告に依存して送信許可を発行しない。
- 送信権の取得・ChatGPT受信・Assistant回答回収を別々に計測する。送信権をリセット・再発行して再検証しない。

## 通常チャットを保つ代替構成

**A（推奨: ユーザー選択チャットでのツール取得）**
Muse/Codexは認証付き外部キューにイベントを保存（既存 `event_queue.py` はオフライン実装）。ユーザーが**意図した通常チャット**で受信指示を出し、ChatGPTがモデル向けMCP取得ツールを実行し、そのツール結果をそのチャットのAssistant応答として扱う。**`ui/message`を実行しない**ため、他チャットに載ってしまったiframeからの誤送信経路を使わない。ただし無人起動はできず、OAuth/権限制御と実機のChatGPTでのツール表示の証拠が追加で必要。

**B（草稿経由）**
チャット単位Thread Entrypointでイベントを表示し、ユーザーの明示操作があった場合だけ拡張 `ui/message` の `target:"active",send:false` で**編集可能な草稿**を作る。ユーザーが投稿するチャット・文面を視覚的に確認して通常の送信を実施。送信先の機械的保証にはならないが、意図しない無操作投稿を防げる。実機は未検証、草稿作成も送信元UIに影響があるため別途実験条件を設ける。

**C（完全無人型）**
対象ChatGPT通常チャットを特定する、プラットフォーム署名付きのRecipient-bound送信・応答通知・外部イベント起動が提供されるまで有効化しない。別のAPIチャットに置き換える提案は目的外。

## 受入判定と未完

| 条件 | 現在 |
| --- | --- |
| 標準とOpenAI拡張での対象指定の調査 | 完了（上記） |
| Thread Entrypointコード／ローカルMCP | 検証中（送信なし） |
| iPhone/Windowsの新しいThread Entrypoint | 未検証 |
| 認証された通常チャットIDと原子的な宛先固定 | 仕様上未確認 |
| 既存senderの未検証宛先に対するfail-closed | 実装・テスト |
| 外部イベントから通常チャットへの完全無人起動 | 未検証 |
| 固定文1通の実機送信 | **安全条件未達のため未実施** |

実機観測の不足を理由に、既存チャットの旧ウィジェットを再実行しないこと。異なるチャットにウィジェットが現れた経緯から、1回の正常送信だけで宛先保証を達成したと判断しないこと。
