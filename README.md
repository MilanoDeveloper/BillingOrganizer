# Billing Organizer

## Launcher local no Windows

Para iniciar backend, frontend e abrir o navegador automaticamente, clique duas vezes em `iniciar-billing-organizer.ps1` ou execute no PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\iniciar-billing-organizer.ps1
```

O launcher abre duas janelas do PowerShell e o navegador em `http://localhost:4200`.

Para encerrar os processos do projeto:

```powershell
.\parar-billing-organizer.ps1
```

Para criar um atalho na área de trabalho: clique com o botão direito em `iniciar-billing-organizer.ps1`, escolha **Mostrar mais opções > Criar atalho** e mova o atalho para a Área de Trabalho. Se o Windows não permitir criar o atalho diretamente, crie um atalho apontando para:

```text
powershell.exe -ExecutionPolicy Bypass -File "C:\dev\repositorios\billingOrganizer\iniciar-billing-organizer.ps1"
```
