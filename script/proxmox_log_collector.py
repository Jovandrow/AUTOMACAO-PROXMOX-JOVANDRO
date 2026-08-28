#!/usr/bin/env python3
"""
=============================================================
  Proxmox Log Collector + Zabbix Monitor
  Autor: Jovandro junior
  Descricao: Automatizacao do monitoramento no Proxmox(pode servir de modelo para outros ambientes como o Nutanix)
=============================================================

  CONFIGURACAO NECESSARIA:
  1. Preencha as variaveis na secao CONFIGURACAO abaixo
  2. Crie um token de API no Proxmox com role PVEAuditor
  3. Configure SSH sem senha entre o Zabbix e o Proxmox
  4. Crie um bot no Telegram via @BotFather
  5. Agende via crontab -e:
     0 8,10,12,17,22 * * * /usr/bin/python3 /etc/zabbix/scripts/proxmox_log_collector.py 1440

  DEPENDENCIAS:
  pip3 install requests
=============================================================
"""

import requests
import sys
import subprocess
from datetime import datetime, timedelta
import urllib3
urllib3.disable_warnings()

# ─── CONFIGURACAO ───────────────────────────────────────────
PROXMOX_HOST     = "https://SEU-IP:8006"
TOKEN_ID         = "SEU-TOKEN-ID"
TOKEN_SECRET     = "SEU-TOKEN-SECRET"
NODE             = "SEU-NODE"

ZABBIX_URL       = "URL-ZABBIX"
ZABBIX_USER      = "SEU-USUARIO-ZABBIX"
ZABBIX_PASS      = "SUA-SENHA-ZABBIX"

TELEGRAM_TOKEN   = "SEU-TELEGRAM-BOT-TOKEN"
TELEGRAM_CHAT_ID = "SEU-CHAT-ID"

PROXMOX_SSH      = "IP-PROXMOX"

# Limites de alerta
LIMIT_CPU        = 80.0   # %
LIMIT_MEM        = 90.0   # %
LIMIT_DISK       = 100.0  # MB/s
# ────────────────────────────────────────────────────────────

HEADERS = {"Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"}

def zabbix_login():
    r = requests.post(ZABBIX_URL, json={
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {"username": ZABBIX_USER, "password": ZABBIX_PASS},
        "id": 1
    })
    return r.json().get("result")

def get_zabbix_alerts(token):
    since = int((datetime.now() - timedelta(hours=24)).timestamp())
    r = requests.post(ZABBIX_URL, json={
        "jsonrpc": "2.0",
        "method": "problem.get",
        "params": {
            "output": "extend",
            "selectAcknowledges": "count",
            "recent": True,
            "time_from": since,
            "sortfield": ["eventid"],
            "sortorder": "ASC"
        },
        "auth": token,
        "id": 2
    })
    return r.json().get("result", [])

def get_zabbix_items(token):
    r = requests.post(ZABBIX_URL, json={
        "jsonrpc": "2.0",
        "method": "item.get",
        "params": {
            "output": ["name", "lastvalue", "units"],
            "search": {
                "name": ["CPU usage", "Memory usage", "Memory total", "Disk read, rate", "Disk write, rate"]
            },
            "searchByAny": True,
            "monitored": True,
            "limit": 500
        },
        "auth": token,
        "id": 3
    })
    return r.json().get("result", [])

def check_thresholds(items):
    alerts = []

    for item in items:
        name  = item.get("name", "")
        value = item.get("lastvalue", "0")
        try:
            val = float(value)
        except:
            continue

        if "CPU usage" in name:
            if val >= LIMIT_CPU:
                alerts.append(f"  [!] CPU ALTA       : {name}\n      Valor: {val:.1f}%  |  Limite: {LIMIT_CPU}%")

        elif "Disk read, rate" in name or "Disk write, rate" in name:
            val_mbs = val / (1024 * 1024)
            if val_mbs >= LIMIT_DISK:
                alerts.append(f"  [!] DISCO ALTO     : {name}\n      Valor: {val_mbs:.1f} MB/s  |  Limite: {LIMIT_DISK} MB/s")

    mem_usage = {}
    mem_total = {}
    for item in items:
        name  = item.get("name", "")
        value = item.get("lastvalue", "0")
        try:
            val = float(value)
        except:
            continue
        if "Memory usage" in name:
            mem_usage[name] = val
        elif "Memory total" in name:
            mem_total[name] = val

    for usage_name, usage_val in mem_usage.items():
        vm_prefix = usage_name.replace("Memory usage", "")
        total_key = vm_prefix + "Memory total"
        if total_key in mem_total and mem_total[total_key] > 0:
            pct = (usage_val / mem_total[total_key]) * 100
            if pct >= LIMIT_MEM:
                alerts.append(f"  [!] MEMORIA ALTA   : {vm_prefix.strip()}\n      Valor: {pct:.1f}%  |  Limite: {LIMIT_MEM}%")

    return alerts

def get_tasks(minutes=1440):
    since = int((datetime.now() - timedelta(minutes=minutes)).timestamp())
    url   = f"{PROXMOX_HOST}/api2/json/nodes/{NODE}/tasks"
    r     = requests.get(url, headers=HEADERS, params={"limit": 500}, verify=False)
    tasks = r.json().get("data", [])
    return [t for t in tasks if t.get("starttime", 0) >= since]

def get_proxmox_syslogs(minutes=1440):
    since     = datetime.now() - timedelta(minutes=minutes)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    keywords  = "error|warning|critical|fail|pci|oom|disk|raid|hardware|temperature|voltage|fan|authentication failure"

    cmd = (
        f"ssh -o StrictHostKeyChecking=no {PROXMOX_SSH} "
        f"\"journalctl --since '{since_str}' -p warning --no-pager -q "
        f"| grep -iE '{keywords}' | tail -50\""
    )

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines  = result.stdout.strip().split("\n") if result.stdout.strip() else []
    return [l for l in lines if l.strip()]

def format_report(tasks, minutes, threshold_alerts, zabbix_problems, syslogs):
    now       = datetime.now()
    since     = now - timedelta(minutes=minutes)
    separator = "=" * 65
    divisor   = "-" * 65

    lines = []

    lines.append(separator)
    lines.append("        COLETA DE LOG PROXMOX")
    lines.append(f"        Time: {now.strftime('%d/%m/%Y %H:%M:%S')}")
    lines.append(f"        Periodo: {since.strftime('%d/%m/%Y %H:%M')} ate {now.strftime('%d/%m/%Y %H:%M')}")
    lines.append(f"        Node: {NODE}  |  Host: {PROXMOX_HOST}")
    lines.append(separator)
    lines.append("")

    status = "NORMAL" if not threshold_alerts and not zabbix_problems else "ATENCAO - PROBLEMAS DETECTADOS"
    lines.append(f"  STATUS GERAL    : {status}")
    lines.append(f"  Eventos Proxmox : {len(tasks)}")
    lines.append(f"  Alertas recurso : {len(threshold_alerts)}")
    lines.append(f"  Problemas Zabbix: {len(zabbix_problems)} (ultimas 24h)")
    lines.append(f"  Erros syslog    : {len(syslogs)}")
    lines.append("")

    lines.append(separator)
    lines.append("  [1] ALERTAS DE UTILIZACAO DE RECURSOS")
    lines.append(divisor)
    if threshold_alerts:
        for a in threshold_alerts:
            lines.append(a)
            lines.append("")
    else:
        lines.append("  OK - Todos os recursos dentro do normal.")
    lines.append("")

    lines.append(separator)
    lines.append("  [2] PROBLEMAS ATIVOS NO ZABBIX (ultimas 24h)")
    lines.append(divisor)
    if zabbix_problems:
        lines.append(f"  {'DATA/HORA':<17} DESCRICAO")
        lines.append(f"  {'-'*15}  {'-'*44}")
        for p in sorted(zabbix_problems, key=lambda x: int(x.get("clock", 0))):
            ts   = datetime.fromtimestamp(int(p.get("clock", 0))).strftime("%d/%m/%Y %H:%M")
            name = p.get("name", "")
            lines.append(f"  {ts:<17} {name}")
    else:
        lines.append("  OK - Nenhum problema ativo nas ultimas 24h.")
    lines.append("")

    lines.append(separator)
    lines.append(f"  [3] ACESSOS E ACOES NO PROXMOX  ({len(tasks)} eventos)")
    lines.append(divisor)
    if tasks:
        lines.append(f"  {'TIMESTAMP':<20} {'USUARIO':<20} {'ACAO':<15} {'VM':<8} STATUS")
        lines.append(f"  {'-'*19} {'-'*19} {'-'*14} {'-'*7} {'-'*20}")
        for t in sorted(tasks, key=lambda x: x.get("starttime", 0), reverse=False):
            ts     = datetime.fromtimestamp(t["starttime"]).strftime("%d/%m/%Y %H:%M:%S")
            user   = t.get("user", "desconhecido")
            ttype  = t.get("type", "")
            vmid   = t.get("id", "")
            status = t.get("status", "")
            lines.append(f"  {ts:<20} {user:<20} {ttype:<15} {vmid:<8} {status}")
    else:
        lines.append("  Nenhuma atividade registrada no periodo.")
    lines.append("")

    lines.append(separator)
    lines.append("  [4] ERROS E ALERTAS DO SERVIDOR (syslog)")
    lines.append(divisor)
    if syslogs:
        for log in syslogs:
            lines.append(f"  {log}")
    else:
        lines.append("  OK - Nenhum erro critico encontrado.")
    lines.append("")
    lines.append(separator)
    lines.append(f"  Fim do relatorio  -  {now.strftime('%d/%m/%Y %H:%M:%S')}")
    lines.append(separator)

    return "\n".join(lines)

def send_file(filepath, filename, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    with open(filepath, "rb") as f:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption
        }, files={"document": (filename, f)})
    return r.json()

if __name__ == "__main__":
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 1440

    print("Autenticando no Zabbix...")
    zabbix_token = zabbix_login()

    print("Coletando problemas do Zabbix...")
    zabbix_problems = get_zabbix_alerts(zabbix_token)

    print("Verificando thresholds...")
    items            = get_zabbix_items(zabbix_token)
    threshold_alerts = check_thresholds(items)

    print("Coletando logs do Proxmox...")
    tasks = get_tasks(minutes)

    print("Coletando syslog do servidor Proxmox...")
    syslogs = get_proxmox_syslogs(minutes)

    report   = format_report(tasks, minutes, threshold_alerts, zabbix_problems, syslogs)
    ts_file  = datetime.now().strftime("%d-%m-%Y_%H-%M")
    filename = f"Coleta_de_Log_Proxmox_{ts_file}.txt"
    filepath = f"/tmp/{filename}"

    with open(filepath, "w") as f:
        f.write(report)

    print(f"Arquivo gerado: {filepath}")

    status_icon = "🚨" if threshold_alerts or zabbix_problems else "✅"
    caption = (
        f"{status_icon} Coleta de Log Proxmox\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"📊 Eventos: {len(tasks)}\n"
        f"⚠️  Alertas: {len(threshold_alerts)}\n"
        f"🔴 Problemas Zabbix: {len(zabbix_problems)}\n"
        f"📋 Erros syslog: {len(syslogs)}"
    )

    result = send_file(filepath, filename, caption)
    if result.get("ok"):
        print("Enviado com sucesso pro Telegram!")
    else:
        print(f"Erro: {result}")
