#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# One-shot deployment/boot policy, not a resident service or steering daemon.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077

DEFAULTS=/usr/share/dicttech-ap/defaults.json
MARKER=/etc/dicttech-ap/device.json
ALLOWED='network sysntpd firewall dropbear uhttpd xr_roaming sysctl cpufreq airoha-npu fan dnsmasq odhcpd dawn usteer ttyd watchcat umdns npu-jitter wifihistory zerotier miniupnpd ddns microsocks adguardhome AdGuardHome openclash passwall passwall2 shadowsocksr lucky autoreboot nlbwmon bandix pushbot radius 3proxy'

allowed() {
 case " $ALLOWED " in *" $1 "*) return 0;; *) return 1;; esac
}

running_state() {
 local data
 data=$(ubus call service list "{\"name\":\"$1\"}" 2>/dev/null) || return 1
 printf '%s\n' "$data" |
  jq -er 'select(type=="object") | [.[]?.instances[]? | .running==true] | any | tostring'
}

rc_links() {
 local service=$1 link name target
 for link in /etc/rc.d/S[0-9][0-9]"$service" /etc/rc.d/K[0-9][0-9]"$service"; do
  [ -L "$link" ] || continue
  name=${link##*/}; target=$(readlink "$link") || return 1
  case "$target" in "../init.d/$service"|"/etc/init.d/$service") ;;
   *) printf 'dicttech-ap: unexpected service link: %s\n' "$name" >&2; return 1;;
  esac
  jq -cn --arg name "$name" --arg target "$target" '{name:$name,target:$target}' || return 1
 done
}

snapshot() {
 local dest=$1 tmp service present enabled active links
 case "$dest" in /*) ;; *) return 2;; esac
 [ ! -L "$dest" ] || return 1
 tmp=$dest.$$.tmp
 [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 : > "$tmp"
 for service in $ALLOWED; do
  present=false; enabled=false; active=false
  if [ -x "/etc/init.d/$service" ]; then
   present=true
   /etc/init.d/"$service" enabled >/dev/null 2>&1 && enabled=true
   active=$(running_state "$service") || { rm -f "$tmp"; return 1; }
  fi
  links=$(rc_links "$service") || { rm -f "$tmp"; return 1; }
  links=$(printf '%s\n' "$links" | jq -sc '.') || { rm -f "$tmp"; return 1; }
  jq -cn --arg service "$service" --argjson present "$present" --argjson enabled "$enabled" \
   --argjson running "$active" --argjson links "$links" \
   '{service:$service,present:$present,enabled:$enabled,running:$running,links:$links}' >> "$tmp" || { rm -f "$tmp"; return 1; }
 done
 [ ! -e "$tmp.json" ] && [ ! -L "$tmp.json" ] || { rm -f "$tmp"; return 1; }
 jq -sc '{schema_version:1,services:.}' "$tmp" > "$tmp.json" || return 1
 chmod 600 "$tmp.json" || return 1
 mv "$tmp.json" "$dest" || return 1
 rm -f "$tmp"
}

policy() {
 local source=$DEFAULTS mode=${1:-managed} service enabled disabled disabled_words
 if [ "$mode" != setup ] && [ -f "$MARKER" ]; then source=$MARKER; fi
 jq -e --arg allowed "$ALLOWED" '
   .services as $p |
   ($p|type=="object") and ($p.enabled|type=="array") and ($p.disabled|type=="array") and
   (($p.enabled+$p.disabled) as $all |
     ($all|length)==($all|unique|length) and
     all($all[]; . as $s | ($s|type=="string") and ($allowed|split(" ")|index($s))!=null))
 ' "$source" >/dev/null || return 1
 enabled=$(jq -er '.services.enabled | select(type=="array") | .[]' "$source") || return 1
 disabled=$(jq -er '.services.disabled | select(type=="array") | .[]' "$source") || return 1
 disabled_words=$(printf '%s' "$disabled" | tr '\n' ' ')
 for service in $enabled $disabled; do allowed "$service" || return 1; done
 for service in $enabled; do
  case " $disabled_words " in *" $service "*) return 1;; esac
 done
 for service in $enabled; do
  [ "$mode:$service" != setup:xr_roaming ] || continue
  printf 'enable %s\n' "$service" || return 1
 done
 # Hardware support services are package-specific and optional when absent.
 for service in sysctl cpufreq airoha-npu fan; do printf 'enable %s\n' "$service" || return 1; done
 for service in $disabled; do printf 'disable %s\n' "$service" || return 1; done
 [ "$mode" != setup ] || printf '%s\n' 'disable xr_roaming'
}

apply_policy() {
 local phase=$1 rows action service active
 rows=$(policy "${2:-managed}") || return 1
 printf '%s\n' "$rows" | while IFS=' ' read -r action service; do
  [ -x "/etc/init.d/$service" ] || continue
  if [ "$action" = disable ]; then
   /etc/init.d/"$service" disable || return 1
   if [ "$phase" != links ]; then /etc/init.d/"$service" stop || return 1; fi
  else
   /etc/init.d/"$service" enable || return 1
   if [ "$phase" != links ]; then
    active=$(running_state "$service") || return 1
    if [ "$active" = false ]; then /etc/init.d/"$service" start || return 1; fi
   fi
  fi
 done
}

validate_snapshot() {
 local file=$1 service name target rows
 [ -f "$file" ] && [ ! -L "$file" ] || return 1
 jq -e '.schema_version==1 and (.services|type=="array") and
   (.services|all(.[]; (.service|type=="string") and
     (.present|type=="boolean") and (.enabled|type=="boolean") and
     (.running|type=="boolean") and (.links|type=="array")))' "$file" >/dev/null || return 1
 jq -e --arg allowed "$ALLOWED" '[.services[].service]==($allowed|split(" "))' "$file" >/dev/null || return 1
 rows=$(jq -r '.services[] | .service as $s | .links[] | [$s,.name,.target] | @tsv' "$file") || return 1
 printf '%s\n' "$rows" |
 while IFS="$(printf '\t')" read -r service name target; do
  [ -n "$service" ] || continue
  case "$name" in S[0-9][0-9]"$service"|K[0-9][0-9]"$service") ;; *) return 1;; esac
  case "$target" in "../init.d/$service"|"/etc/init.d/$service") ;; *) return 1;; esac
 done
}

restore() {
 local file=$1 phase=$2 service present enabled active actual link name target rows
 validate_snapshot "$file" || return 1
 case "$phase" in
  links)
   rows=$(jq -r '.services[].links[] | [.name,.target] | @tsv' "$file") || return 1
   for service in $ALLOWED; do
    for link in /etc/rc.d/S[0-9][0-9]"$service" /etc/rc.d/K[0-9][0-9]"$service"; do
     [ ! -L "$link" ] || rm "$link" || return 1
    done
   done
   printf '%s\n' "$rows" |
   while IFS="$(printf '\t')" read -r name target; do
    [ -n "$name" ] || continue
    [ ! -e "/etc/rc.d/$name" ] && [ ! -L "/etc/rc.d/$name" ] || return 1
    ln -s "$target" "/etc/rc.d/$name" || return 1
   done
   ;;
  running)
   rows=$(jq -r '.services[] | [.service,(.present|tostring),(.enabled|tostring),(.running|tostring)] | @tsv' "$file") || return 1
   printf '%s\n' "$rows" |
   while IFS="$(printf '\t')" read -r service present enabled active; do
    [ "$present" = true ] && [ -x "/etc/init.d/$service" ] || continue
    case "$service" in
     sysctl|cpufreq|airoha-npu|fan)
      # These apply kernel/hardware settings and have no persistent procd PID.
      # The canonical snapshot order replays restored CPU and fan configuration.
      [ "$enabled" != true ] || /etc/init.d/"$service" start || return 1
      continue
      ;;
    esac
    actual=$(running_state "$service") || return 1
    if [ "$active" = true ] && [ "$actual" != true ]; then
     /etc/init.d/"$service" start || return 1
    elif [ "$active" = false ] && [ "$actual" = true ]; then
     /etc/init.d/"$service" stop || return 1
    fi
   done
   ;;
  *) return 2;;
 esac
}

case "${1:-}" in
 snapshot) [ "$#" -eq 2 ] && snapshot "$2";;
 apply) [ "$#" -eq 1 ] && apply_policy all;;
 links) [ "$#" -eq 1 ] && apply_policy links;;
 setup) [ "$#" -eq 1 ] && apply_policy links setup;;
 restore) [ "$#" -eq 3 ] && restore "$2" "$3";;
 *) printf '%s\n' 'usage: services.sh snapshot FILE|apply|links|setup|restore FILE links|running' >&2; exit 2;;
esac
