# Workspace Intelligence Hub — Portfolio Edition

Workspace Intelligence Hubは、技術資料の検索、顧客ごとに分離されたワークスペースの管理、システム構成の確認、SNS・Web分析データの比較を行う、ローカル運用を前提としたFastAPIアプリケーションです。

このPortfolio Editionは、アプリケーションの構成と自動テストを維持しながら、実際の顧客情報・運用データ・認証情報を合成サンプルへ置き換えた公開版です。

## 主な機能

- 出典表示とワークスペース分離に対応したRAG検索
- Markdown、PDF、Word、Excel、CSV、ソースコード、SQLiteスナップショットの差分インデックス
- インデックス登録前の秘密情報・個人情報検出
- ローカル画像のメタデータ管理、人物ラベル、セマンティック検索
- 認証、権限管理、監査ログ、バックアップ、スケジュール、利用上限
- Meta、Instagram、YouTube、GA4、Googleビジネスプロフィール、LINE、CSV分析との任意連携
- オフラインのRoslynヘルパーによるソースコード静的解析

外部サービス連携は、利用者がGit管理対象外の`.env`へ認証情報を設定するまで無効です。このリポジトリには、実際の認証情報、顧客データ、運用データベース、非公開資料を収録していません。

## 動作環境

- Python 3.12
- Docker Desktop

## 起動手順

PowerShellで次のコマンドを実行します。

```powershell
Copy-Item .env.example .env
Copy-Item sources.example.yaml sources.yaml
docker compose up -d db
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

起動後、`http://127.0.0.1:8000`を開きます。初回アクセス時にローカル管理者を作成します。パスワードは12文字以上で設定してください。

`sample-data/`には、安全にインデックス登録できる合成サンプルを収録しています。公開デモから実運用フォルダーや本番用認証情報を参照しないでください。

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

公開前の検査内容、除外対象、残存リスクは[公開可否レポート](SECURITY_PUBLICATION_REPORT.md)に記録しています。

## セキュリティ上の境界

- `.env`、`sources.yaml`、サービスアカウントファイル、秘密鍵、データベース、バックアップ、アップロード、モデル、実行ログはGit管理対象外です。
- APIは保存済みの認証情報をレスポンスとして返しません。
- 秘密情報検出はルールベースであり、すべての機密情報を検出できることを保証するものではありません。
- 本番設定、顧客資料、運用データの保存にはPrivateリポジトリを使用してください。
