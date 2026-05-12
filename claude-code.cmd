@echo off
wsl -d Ubuntu -- docker exec -it -w /shared/hermes/claude-code/projects/browser-harness claude-code claude --permission-mode bypassPermissions
