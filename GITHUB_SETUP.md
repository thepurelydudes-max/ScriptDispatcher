# Создать GitHub-репозиторий для Codex

## Через сайт GitHub
1. Войдите на https://github.com/new.
2. Repository name: `ScriptDispatcher`. Visibility: **Private**.
3. НЕ создавайте README, .gitignore и LICENSE автоматически: они уже есть в подготовленном проекте.
4. Нажмите Create repository и скопируйте адрес вида `https://github.com/<ваш-логин>/ScriptDispatcher.git`.
5. На ПК распакуйте этот ZIP в отдельную папку, откройте терминал внутри неё и выполните:

```powershell
git init -b main
git add .
git commit -m "Initial GitHub-safe ScriptDispatcher v1.2.17"
git remote add origin https://github.com/<ваш-логин>/ScriptDispatcher.git
git push -u origin main
```

Если Git не установлен, установите Git for Windows с https://git-scm.com/download/win. GitHub может потребовать авторизацию через браузер или Git Credential Manager. Вместо команд можно использовать GitHub Desktop: File -> Add local repository -> Create a repository -> Publish repository (снимите флажок публичности).

## В Codex
- Подключите свой GitHub-аккаунт и выберите репозиторий `ScriptDispatcher`.
- Передайте агенту задачу и попросите прочесть `AGENTS.md`.
- Работайте через изменения/ветки и pull requests, а не непосредственную замену установленного EXE.

## Сборка в GitHub Actions
- GitHub -> репозиторий -> Actions -> `Windows EXE Build` -> `Run workflow`.
- После завершения откройте запуск и скачайте артефакт `ScriptDispatcher-Windows-x64`.
- Он содержит `ScriptDispatcher.exe` и зависимости, но НЕ содержит вашу `data` и секреты.
- После распаковки перенесите резервную копию вашей рабочей `data` рядом с EXE. Не делитесь ею и не загружайте в GitHub.
- Тестовый workflow запускается автоматически на push и pull request.

## Приватные данные
- Не загружайте старую ZIP-сборку v1.2.16 напрямую: она содержит реальные значения ключей/Cookie.
- Новый архив очищен от этих двух исходных файлов, а `.gitignore` исключает локальное `data/`, Cookie, сборки и виртуальное окружение.
- Проверяйте `git status` и содержимое коммита перед отправкой. Если секрет случайно был опубликован в истории Git, одного удаления файла недостаточно — потребуется заменить/отозвать секрет.
