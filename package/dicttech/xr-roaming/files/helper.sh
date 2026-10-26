#!/bin/sh
# Site-specific C7 neighbor exchange. No wireless/UCI writes or client steering.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
ROOT=/usr/lib/xr-roaming
STATE=/tmp/xr-roaming-private
PUBLIC=/tmp/xr-roaming-public
BASE_STATE=/tmp/xr-roaming-private
BASE_PUBLIC=/tmp/xr-roaming-public
WEB=/www/xr-roaming
CTRL=/var/run/hostapd
CLI=/usr/sbin/hostapd_cli
UCI=/sbin/uci
UBUS=/bin/ubus
JQ=/usr/bin/jq
CURL=/usr/bin/curl
TIMEOUT=/usr/bin/timeout
UPTIME=/proc/uptime
UUID=/proc/sys/kernel/random/uuid
CONFIGDIR=/etc/config
DELTADIR=/tmp/.uci
COLLECT_INTERVAL=300
PEER_INTERVAL=60

roam_sleep_pid=""
status_collect=""; status_fetch=""; status_apply=""
status_last_error=""; status_last_error_at=""; status_previous=""
status_write_failed=0
cycle_state=starting; cycle_reason=starting; fetch_reason=not_attempted
config_ready=0; publish_ready=0; force_apply=1; full_due=0; peer_due=0
cached_source_ip=""; publish_suffix=""; status_check=""
roam_fleet=0
full_scans=0; cache_hits=0

now() {
 local tick idle
 IFS=' ' read -r tick idle < "$UPTIME" || return 1
 tick=${tick%%.*}
 case "$tick" in ""|*[!0-9]*) return 1;; esac
 printf '%s\n' "$tick"
}
note() { printf '%s [%s] %s\n' "$(now)" "$ROAM_PROFILE" "$*" >&2; }
get() { "$UCI" -q get "xr_roaming.$ROAM_PROFILE.$1" 2>/dev/null || true; }
select_profile() {
 ROAM_PROFILE=$1
 case "$ROAM_PROFILE" in
  main) EXPECTED_IFACE=ap-mld1; ROAM_MLD=true; ROAM_LIMIT=3;;
  battalion) EXPECTED_IFACE=ap-mld0; ROAM_MLD=true; ROAM_LIMIT=2;;
  guest) EXPECTED_IFACE=ap-mld2; ROAM_MLD=true; ROAM_LIMIT=2;;
  iot) EXPECTED_IFACE=phy0.0-ap1; ROAM_MLD=false; ROAM_LIMIT=1;;
  *) return 1;;
 esac
 STATE=$BASE_STATE; PUBLIC=$BASE_PUBLIC; URI=own.json
 if [ "$ROAM_PROFILE" != main ]; then
  STATE=$BASE_STATE/$ROAM_PROFILE; PUBLIC=$BASE_PUBLIC/$ROAM_PROFILE
  URI=$ROAM_PROFILE/own.json
 fi
}

load_config() {
 local section name bridge_mac="" raw_interval raw_ttl raw_bssids raw_section raw_ssid raw_iface raw_peer_mode peer_id
 [ "$("$UCI" -q get "xr_roaming.$ROAM_PROFILE" 2>/dev/null)" = roaming ] || return 1
 # Static UCI identity avoids netdev/sysfs/RTNL reads during a driver fault.
 for section in $("$UCI" -q show network 2>/dev/null | sed -n 's/^network\.\([^.=]*\)=device$/\1/p'); do
  name=$("$UCI" -q get "network.$section.name" 2>/dev/null || true)
  if [ "$name" = br-lan ]; then
   [ -z "$bridge_mac" ] || return 1
   bridge_mac=$("$UCI" -q get "network.$section.macaddr" 2>/dev/null | tr A-F a-f)
  fi
 done
 raw_peer_mode=$(get peer_mode)
 roam_fleet=0
 printf '%s\n' '[]' > "$STATE/fleet.config.next" || return 1
 case "$raw_peer_mode" in
  fleet)
   roam_fleet=1
   : > "$STATE/fleet.config.lines" || return 1
   for peer_id in $(get peers); do
    case "$peer_id" in ""|*[!A-Za-z0-9_]* ) return 1;; esac
    [ "${#peer_id}" -le 64 ] || return 1
    [ "$("$UCI" -q get "xr_roaming.$peer_id" 2>/dev/null)" = peer ] || return 1
    "$JQ" -cn --arg id "$peer_id" \
     --arg address "$("$UCI" -q get "xr_roaming.$peer_id.address" 2>/dev/null)" \
     --arg mac "$("$UCI" -q get "xr_roaming.$peer_id.bridge_mac" 2>/dev/null | tr A-F a-f)" \
     --arg bssids "$("$UCI" -q get "xr_roaming.$peer_id.${ROAM_PROFILE}_bssid" 2>/dev/null)" \
     '{id:$id,address:$address,bridge_mac:$mac,bssids:($bssids|ascii_downcase|split(" ")|map(select(length>0)))}' \
     >> "$STATE/fleet.config.lines" || return 1
   done
   "$JQ" -cs '.' "$STATE/fleet.config.lines" > "$STATE/fleet.config.next" || return 1
   ;;
  "") :;;
  *) return 1;;
 esac
 raw_interval=$(get interval); raw_ttl=$(get ttl); raw_bssids=$(get peer_bssid)
 raw_section=$(get section); raw_iface=$(get iface)
 if [ -n "$raw_section" ]; then
  case "$raw_section" in *[!A-Za-z0-9_]* ) return 1;; esac
  [ "${#raw_section}" -le 64 ] || return 1
  [ "$("$UCI" -q get "wireless.$raw_section" 2>/dev/null)" = wifi-iface ] || return 1
  raw_ssid=$("$UCI" -q get "wireless.$raw_section.ssid" 2>/dev/null) || return 1
 elif [ "$ROAM_PROFILE" = main ]; then
  # One-profile legacy config compatibility; v2 bundles reference named SSID sections.
  raw_ssid=$(get ssid)
 else return 1
 fi
 [ -n "$raw_iface" ] || { [ "$ROAM_PROFILE" = main ] && raw_iface=$EXPECTED_IFACE; } || return 1
 case "$raw_interval:$raw_ttl" in *[!0-9:]*|:*|*:) return 1;; esac
 "$JQ" -cn --arg enabled "$(get enabled)" --arg ssid "$raw_ssid" \
  --arg profile "$ROAM_PROFILE" --arg section "$raw_section" --arg iface "$raw_iface" \
  --argjson mld "$ROAM_MLD" --argjson max_links "$ROAM_LIMIT" \
  --arg peer "$(get peer)" --arg peer_mac "$(get peer_mac | tr A-F a-f)" \
  --arg self_mac "$bridge_mac" --arg allowed "$raw_bssids" \
  --argjson interval "$raw_interval" --argjson ttl "$raw_ttl" \
  --arg peer_mode "$raw_peer_mode" --slurpfile peers "$STATE/fleet.config.next" \
  '{enabled:$enabled,profile:$profile,section:$section,iface:$iface,mld:$mld,max_links:$max_links,
    ssid:$ssid,peer:$peer,peer_mac:$peer_mac,self_mac:$self_mac,
    peer_bssids:($allowed|ascii_downcase|split(" ")|map(select(length>0))),
    interval:$interval,ttl:$ttl} |
    if $peer_mode=="fleet" then del(.peer,.peer_mac,.peer_bssids)+{peer_mode:"fleet",peers:$peers[0]} else . end' > "$STATE/config.next" || return 1
 "$JQ" -e -L "$ROOT" 'include "common"; if .peer_mode=="fleet" then fleet_config_ok else config_ok end' "$STATE/config.next" >/dev/null || return 1
 if [ -r "$STATE/config.json" ] && ! cmp -s "$STATE/config.next" "$STATE/config.json"; then
  rm -f "$STATE/peer.json" "$STATE/peer.last-good" "$STATE/peer.request.json"
  rm -rf "$STATE/peers"
  rm -f "$STATE/fleet.requests.applied" "$STATE/fleet.status.json"
 fi
 mv "$STATE/config.next" "$STATE/config.json" || return 1
 # Validated SSIDs cannot contain LF. Preserve whitespace/backslashes without eval.
 "$JQ" -r -L "$ROOT" 'include "common";
   .ssid,(.peer//""),(.peer_mac//""),.self_mac,.interval,.ttl,((.peer_bssids//[])|tojson),.iface,(.ssid|ssid_hex)' \
   "$STATE/config.json" > "$STATE/config.fields" || return 1
 {
  IFS= read -r roam_ssid && IFS= read -r roam_peer &&
  IFS= read -r roam_peer_mac && IFS= read -r roam_self_mac &&
  IFS= read -r roam_interval && IFS= read -r roam_ttl &&
  IFS= read -r roam_allowed && IFS= read -r roam_iface &&
  IFS= read -r roam_ssidhex
 } < "$STATE/config.fields" || return 1
 if [ "$roam_fleet" = 1 ]; then
  "$JQ" -r '.peers[] | .id,.address,.bridge_mac,(.bssids|tojson)' "$STATE/config.json" > "$STATE/fleet.fields" || return 1
 fi
}

tools() {
 local p utility
 for p in "$CLI" "$UCI" "$UBUS" "$JQ" "$CURL" "$TIMEOUT"; do [ -x "$p" ] || return 1; done
 for utility in flock sha256sum cut sed tr wc cmp mkdir chmod mv rm cat readlink sleep ls awk pidof cp; do
  command -v "$utility" >/dev/null 2>&1 || return 1
 done
 [ -r "$ROOT/common.jq" ] && [ -r "$ROOT/cli-report.jq" ] || return 1
 [ -r "$UPTIME" ] && [ -r "$UUID" ] || return 1
}

private_dirs() {
 local dir
 umask 077
 for dir in "$BASE_STATE" "$BASE_PUBLIC" "$STATE" "$PUBLIC"; do
  [ ! -L "$dir" ] || return 1
  mkdir -p "$dir"
  [ "$(ls -nd "$dir" | awk '{print $3}')" = 0 ] || return 1
 done
 chmod 700 "$BASE_STATE" "$STATE"; chmod 755 "$BASE_PUBLIC" "$PUBLIC"
}

config_signature() {
 local name
 set -- "$CONFIGDIR/xr_roaming" "$CONFIGDIR/wireless" "$CONFIGDIR/network"
 for name in xr_roaming wireless network; do
  [ ! -f "$DELTADIR/$name" ] || set -- "$@" "$DELTADIR/$name"
 done
 [ ! -f "$BASE_STATE/reload.token" ] || set -- "$@" "$BASE_STATE/reload.token"
 sha256sum "$@" > "$STATE/config.signature.next"
}

topology_probe() {
 local dest=$1 path socket key value enabled n=0 failed=0
 : > "$dest"
 pidof hostapd >> "$dest" 2>/dev/null || true
 if [ "$ROAM_MLD" = true ]; then
  set -- "$CTRL/$roam_iface"_link[0-9] "$CTRL/$roam_iface"_link[0-9][0-9]
 else set -- "$CTRL/$roam_iface"
 fi
 for path in "$@"; do
  [ -S "$path" ] || continue
  n=$((n+1)); socket=${path##*/}
  printf 'socket=%s\n' "$socket" >> "$dest"
  ls -diL "$path" >> "$dest" 2>/dev/null || return 1
  if ! "$TIMEOUT" 3 "$CLI" -p "$CTRL" -i "$socket" status > "$STATE/probe.status" 2>/dev/null; then
   printf 'unavailable\n' >> "$dest"; failed=1; continue
  fi
  enabled=0
  while IFS='=' read -r key value; do
   case "$key" in
    state) [ "$value" != ENABLED ] || enabled=1; printf '%s=%s\n' "$key" "$value" >> "$dest";;
    freq|channel|secondary_channel|vht_oper_chwidth|he_oper_chwidth|eht_oper_chwidth|vht_oper_centr_freq_seg0_idx|he_oper_centr_freq_seg0_idx|eht_oper_centr_freq_seg0_idx|num_links|link_id|link_addr|bss\[*|bssid\[*|ssid\[*|mld_addr\[*|mld_id\[*|mld_link_id\[*|partner_link\[*)
     printf '%s=%s\n' "$key" "$value" >> "$dest";;
   esac
  done < "$STATE/probe.status"
  [ "$enabled" = 1 ] || failed=1
 done
 [ "$n" -gt 0 ] && [ "$failed" = 0 ]
}

collect() {
 local path socket base n=0
 : > "$STATE/links.jsonl"
 # Enumerate only this profile's configured local interface, never the whole radio.
 if [ "$ROAM_MLD" = true ]; then
  set -- "$CTRL/$roam_iface"_link[0-9] "$CTRL/$roam_iface"_link[0-9][0-9]
 else set -- "$CTRL/$roam_iface"
 fi
 for path in "$@"; do
  [ -S "$path" ] || continue
  socket=${path##*/}
  case "$socket" in *[!A-Za-z0-9_.-]*) continue;; esac
  if ! "$TIMEOUT" 3 "$CLI" -p "$CTRL" -i "$socket" status > "$STATE/status.txt" 2>/dev/null; then
   continue
  fi
  # STATUS contains every BSS on the radio: bind to this exact MLD and link ID.
  if ! "$JQ" -ecn -L "$ROOT" --arg socket "$socket" --arg ssid "$roam_ssid" \
    --arg iface "$roam_iface" --argjson mld "$ROAM_MLD" \
    --arg ssidhex "$roam_ssidhex" --arg mode status --rawfile status "$STATE/status.txt" \
    --arg neighbors "" -f "$ROOT/cli-report.jq" > "$STATE/status-own.json" 2>/dev/null; then
   continue
  fi
  "$TIMEOUT" 3 "$CLI" -p "$CTRL" -i "$socket" show_neighbor > "$STATE/neighbors.txt" 2>/dev/null || return 1
  "$TIMEOUT" 3 "$CLI" -p "$CTRL" -i "$socket" status > "$STATE/status-after.txt" 2>/dev/null || return 1
  "$JQ" -ecn -L "$ROOT" --arg socket "$socket" --arg ssid "$roam_ssid" \
    --arg iface "$roam_iface" --argjson mld "$ROAM_MLD" \
    --argjson observed true --arg ssidhex "$roam_ssidhex" --arg mode report --rawfile status "$STATE/status.txt" \
    --rawfile neighbors "$STATE/neighbors.txt" --rawfile status_after "$STATE/status-after.txt" \
    -f "$ROOT/cli-report.jq" >> "$STATE/links.jsonl" 2>/dev/null || return 1
  n=$((n + 1))
  [ "$n" -le "$ROAM_LIMIT" ] || return 1
 done
 [ "$n" -gt 0 ] || return 1
 "$JQ" -ecs --argjson mld "$ROAM_MLD" 'sort_by(.link_id) |
   if ([.[].base]|unique|length)==1 and ([.[].bssid]|unique|length)==length and
      ([.[].link_id]|unique|length)==length and all(.[];.mld==$mld) then .
   else error("ambiguous local MLD") end' "$STATE/links.jsonl" > "$STATE/links.next" || return 1
 mv "$STATE/links.next" "$STATE/links.json"
 base=$("$JQ" -r '.[0].base' "$STATE/links.json")
 case "$base" in ""|*[!A-Za-z0-9_.-]*) return 1;; esac
 printf '%s\n' "hostapd.$base" > "$STATE/object"
}

publish() {
 local revision
 IFS= read -r revision < "$UUID" || return 1
 case "$revision" in *[!0-9a-f-]*) return 1;; esac
 case "$revision" in ????????-????-????-????-????????????) :;; *) return 1;; esac
 if [ "$publish_ready" != 1 ]; then
  "$JQ" -cnr --arg ssid "$roam_ssid" --arg mac "$roam_self_mac" --slurpfile own "$STATE/links.json" \
   '{version:1,bridge_mac:$mac,ssid:$ssid,reports:($own[0]|map({bssid,ssid,nr}))}|tojson|.[1:]' \
   > "$STATE/publish.suffix" || return 1
  IFS= read -r publish_suffix < "$STATE/publish.suffix" || return 1
 fi
 # Fixed format; all dynamic local fields were JSON-encoded after strict collection.
 printf '{"revision":"%s",%s\n' "$revision" "$publish_suffix" > "$STATE/own.next" || return 1
 if [ "$publish_ready" != 1 ]; then
  "$JQ" -e -L "$ROOT" --arg ssid "$roam_ssid" --arg mac "$roam_self_mac" --slurpfile own "$STATE/links.json" \
   'include "common"; public_ok($ssid;$mac;[$own[0][].bssid])' "$STATE/own.next" >/dev/null || return 1
  publish_ready=1
 fi
 chmod 644 "$STATE/own.next" && mv "$STATE/own.next" "$PUBLIC/own.json"
}

load_lan_source() {
 "$TIMEOUT" 3 "$UBUS" -t 2 call network.interface.lan status > "$STATE/lan-status.json" 2>/dev/null || {
  fetch_reason=lan_status_unavailable; return 1;
 }
 cached_source_ip=$("$JQ" -er -L "$ROOT" 'include "common";
   select(type=="object" and .up==true and .proto=="dhcp" and .l3_device=="br-lan.1" and
     (."ipv4-address"|type=="array" and length==1) and
     (."ipv4-address"[0].address|private_ipv4)) | ."ipv4-address"[0].address' \
   "$STATE/lan-status.json" 2>/dev/null) || { cached_source_ip=""; fetch_reason=lan_address_invalid; return 1; }
}

fetch_peer_one() {
 local revision previous tick source_ip report_count peer_state=${1:-$STATE}
 fetch_reason=ok
 tick=$(now)
 # Cache the actual DHCP LAN source; invalidate on local changes/full audit/failure.
 [ -n "$cached_source_ip" ] || load_lan_source || return 1
 source_ip=$cached_source_ip
 # Never follow redirects, inherit proxies, use credentials, or accept an arbitrary URL.
 "$CURL" --disable --silent --show-error --fail --noproxy '*' --proto '=http' --max-redirs 0 \
   --interface "$source_ip" \
   --connect-timeout 2 --max-time 5 --max-filesize 16384 \
   --header 'Cache-Control: no-cache' \
   --output "$peer_state/peer.download" "http://$roam_peer/xr-roaming/$URI?r=$tick" 2>/dev/null || {
  cached_source_ip=""
  load_lan_source || return 1
  fetch_reason=peer_download_failed; return 1;
 }
 [ "$(wc -c < "$peer_state/peer.download")" -le 16384 ] || { fetch_reason=peer_size_invalid; return 1; }
 "$JQ" -ec -L "$ROOT" --arg ssid "$roam_ssid" --arg mac "$roam_peer_mac" \
   --argjson allowed "$roam_allowed" --slurpfile own "$STATE/links.json" \
   'include "common"; select(public_ok($ssid;$mac;$allowed)) |
    select(([.reports[].bssid] - [$own[0][].bssid] | length) == (.reports|length))' \
   "$peer_state/peer.download" > "$peer_state/peer.next" 2>/dev/null || { fetch_reason=peer_payload_invalid; return 1; }
 "$JQ" -r '.revision,(.reports|length)' "$peer_state/peer.next" > "$peer_state/peer.fields" || return 1
 { IFS= read -r revision && IFS= read -r report_count; } < "$peer_state/peer.fields" || return 1
 case "$report_count" in 1|2|3) :;; *) fetch_reason=peer_payload_invalid; return 1;; esac
 previous=""
 [ ! -r "$peer_state/peer.json" ] || previous=$("$JQ" -r .revision "$peer_state/peer.json")
 if [ "$revision" != "$previous" ]; then
  mv "$peer_state/peer.next" "$peer_state/peer.json" || return 1
  printf '%s\n' "$tick" > "$peer_state/peer.last-good"
 else
  # A frozen publisher must expire even while its old static JSON remains readable.
  cmp -s "$peer_state/peer.next" "$peer_state/peer.json" || { fetch_reason=peer_revision_conflict; return 1; }
 fi
 "$JQ" -c '{list:(.reports|map([.bssid,.ssid,.nr]))}' "$peer_state/peer.json" > "$peer_state/peer.request.json" || return 1
 printf '%s\n' "$report_count" > "$peer_state/count" || return 1
}

fetch_peer() {
 if [ "$roam_fleet" != 1 ]; then fetch_peer_one; return; fi
 local peer_id peer_state failed=0 roam_peer roam_peer_mac roam_allowed
 fetch_reason=ok
 while IFS= read -r peer_id && IFS= read -r roam_peer && IFS= read -r roam_peer_mac && IFS= read -r roam_allowed; do
  peer_state=$STATE/peers/$peer_id
  [ ! -L "$STATE/peers" ] && [ ! -L "$peer_state" ] || return 1
  mkdir -p "$peer_state" && chmod 700 "$STATE/peers" "$peer_state" || return 1
  if fetch_peer_one "$peer_state"; then :
  else failed=1
  fi
  printf '%s\n' "$fetch_reason" > "$peer_state/result" || return 1
 done < "$STATE/fleet.fields"
 if [ "$failed" = 1 ]; then fetch_reason=peer_download_or_payload_failed; return 1; fi
 fetch_reason=ok
}

request_list_fleet() {
 local tick peer_id peer_address peer_mac allowed peer_state last_good age count result fresh sep="" newest=-1
 tick=$(now)
 : > "$STATE/fleet.requests.next" || return 1
 printf '[' > "$STATE/fleet.status.next" || return 1
 while IFS= read -r peer_id && IFS= read -r peer_address && IFS= read -r peer_mac && IFS= read -r allowed; do
  peer_state=$STATE/peers/$peer_id; last_good=-1; count=0; result=not_attempted; fresh=false
  [ ! -r "$peer_state/peer.last-good" ] || IFS= read -r last_good < "$peer_state/peer.last-good"
  case "$last_good" in ""|*[!0-9]*) last_good=-1;; esac
  [ ! -r "$peer_state/count" ] || IFS= read -r count < "$peer_state/count"
  case "$count" in 0|1|2|3) :;; *) count=0;; esac
  [ ! -r "$peer_state/result" ] || IFS= read -r result < "$peer_state/result"
  case "$result" in ""|*[!a-z_]* ) result=invalid_result;; esac
  age=$((tick-last_good))
  if [ "$last_good" -ge 0 ] && [ "$age" -ge 0 ] && [ "$age" -lt "$roam_ttl" ] && [ -r "$peer_state/peer.request.json" ]; then
   fresh=true
   cat "$peer_state/peer.request.json" >> "$STATE/fleet.requests.next" || return 1
   [ "$last_good" -le "$newest" ] || newest=$last_good
  elif [ "$cycle_state" = ok ]; then cycle_state=degraded; cycle_reason=peer_unavailable
  fi
  printf '%s{"id":"%s","last_revision_uptime":%s,"cached_count":%s,"fresh":%s,"fetch_result":"%s"}' \
   "$sep" "$peer_id" "$last_good" "$count" "$fresh" "$result" >> "$STATE/fleet.status.next" || return 1
  sep=,
 done < "$STATE/fleet.fields"
 # Stable candidate content avoids repeated parsing and writes in quiet homes.
 if [ ! -r "$STATE/peer.request.json" ] || [ ! -r "$STATE/peer.json" ] ||
    ! cmp -s "$STATE/fleet.requests.next" "$STATE/fleet.requests.applied"; then
  "$JQ" -cs '{list:([.[].list[]]|sort_by(.[0]))}' "$STATE/fleet.requests.next" > "$STATE/peer.request.json" || return 1
  cp "$STATE/fleet.requests.next" "$STATE/fleet.requests.applied" || return 1
  "$JQ" -c '{reports:(.list|map({bssid:.[0],ssid:.[1],nr:.[2]}))}' "$STATE/peer.request.json" > "$STATE/peer.json" || return 1
 fi
 if [ ! -r "$STATE/peer.request.json" ]; then
  printf '%s\n' '{"list":[]}' > "$STATE/peer.request.json"
  printf '%s\n' '{"reports":[]}' > "$STATE/peer.json"
 fi
 cp "$STATE/peer.request.json" "$STATE/request.json" || return 1
 if [ "$newest" -ge 0 ]; then printf '%s\n' "$newest" > "$STATE/peer.last-good"
 else rm -f "$STATE/peer.last-good"
 fi
 printf ']\n' >> "$STATE/fleet.status.next" || return 1
 mv "$STATE/fleet.status.next" "$STATE/fleet.status.json" || return 1
}

request_list() {
 [ "$roam_fleet" != 1 ] || { request_list_fleet; return; }
 local tick last_good age
 tick=$(now); last_good=-1
 [ ! -r "$STATE/peer.last-good" ] || read -r last_good < "$STATE/peer.last-good"
 case "$last_good" in ""|*[!0-9]*) last_good=-1;; esac
 age=$((tick - last_good))
 if [ "$last_good" -ge 0 ] && [ "$age" -ge 0 ] && [ "$age" -lt "$roam_ttl" ] && [ -r "$STATE/peer.json" ]; then
  if [ -r "$STATE/peer.request.json" ]; then
   cp "$STATE/peer.request.json" "$STATE/request.json" || return 1
  else
   "$JQ" -c '{list:(.reports|map([.bssid,.ssid,.nr]))}' "$STATE/peer.json" > "$STATE/request.json" || return 1
  fi
 else
  printf '%s\n' '{"list":[]}' > "$STATE/request.json"
  if [ "$cycle_state" = ok ]; then cycle_state=degraded; cycle_reason=peer_unavailable; fi
 fi
}

apply_list() {
 local object
 apply_reason=apply_context_unavailable
 [ -r "$STATE/object" ] || return 1
 read -r object < "$STATE/object"
 case "$object" in hostapd.*) :;; *) return 1;; esac
 case "$object" in *[!A-Za-z0-9_.-]*) return 1;; esac
 "$TIMEOUT" 3 "$UBUS" -t 2 call "$object" get_status > "$STATE/object-status.json" 2>/dev/null || {
  apply_reason=apply_status_unavailable; return 1;
 }
 "$JQ" -e --arg ssid "$roam_ssid" --slurpfile own "$STATE/links.json" \
   '. as $s | $s.ssid==$ssid and $s.status=="ENABLED" and
    (($s.bssid|ascii_downcase) as $b | [$own[0][].bssid] | index($b)!=null)' \
   "$STATE/object-status.json" >/dev/null 2>&1 || { apply_reason=apply_identity_mismatch; return 1; }
 # C7's rrm_nr_set_mld applies this *one complete list* to all MLD links and
 # retains it for links joining later. No per-neighbor overwrite loop.
 "$TIMEOUT" 4 "$UBUS" -t 3 call "$object" rrm_nr_set "$(cat "$STATE/request.json")" \
   > "$STATE/set-result.json" 2>/dev/null || { apply_reason=apply_write_failed; return 1; }
 apply_reason=ok
}

clear_tables() {
 local clear_rc=0
 rm -f "$PUBLIC/own.json"
 if [ -r "$STATE/config.json" ] && [ -r "$STATE/links.json" ] && [ -r "$STATE/object" ]; then
  roam_ssid=$("$JQ" -r .ssid "$STATE/config.json")
  printf '%s\n' '{"list":[]}' > "$STATE/request.json"
  apply_list || { note "neighbor clear unavailable"; clear_rc=1; }
 fi
 rm -f "$STATE/peer.json" "$STATE/peer.last-good" "$STATE/peer.request.json" "$STATE/applied.request.json"
 rm -rf "$STATE/peers"
 rm -f "$STATE/fleet.requests.applied" "$STATE/fleet.status.json"
 config_ready=0; publish_ready=0; force_apply=1; cached_source_ip=""
 return "$clear_rc"
}

audit_matches() {
 "$JQ" -e --arg hex "$roam_ssidhex" --slurpfile request "$STATE/request.json" '
  ($request[0].list|map({bssid:.[0],ssidhex:$hex,nr:.[2]})|sort_by(.bssid)) as $expected |
  length>0 and all(.[]; .observed==$expected)' "$STATE/links.json" >/dev/null 2>&1
}

write_status() {
 local tick own_file=/dev/null peer_file=/dev/null request_file=/dev/null marker_file=/dev/null fleet_file=/dev/null transition
 tick=$(now)
 [ ! -r "$STATE/links.json" ] || own_file=$STATE/links.json
 [ ! -r "$STATE/peer.json" ] || peer_file=$STATE/peer.json
 [ ! -r "$STATE/request.json" ] || request_file=$STATE/request.json
 [ ! -r "$STATE/peer.last-good" ] || marker_file=$STATE/peer.last-good
 [ ! -r "$STATE/fleet.status.json" ] || fleet_file=$STATE/fleet.status.json
 if [ "$cycle_state" != ok ] && [ "$cycle_state" != stopped ]; then
  status_last_error=$cycle_reason; status_last_error_at=$tick
 fi
 "$JQ" -cn --arg profile "$ROAM_PROFILE" --arg mode "${mode:-unknown}" --arg state "$cycle_state" --arg reason "$cycle_reason" \
  --arg fetch_result "$fetch_reason" --arg pid "$$" --argjson tick "$tick" \
  --argjson interval "${roam_interval:-30}" --argjson ttl "${roam_ttl:-0}" \
  --arg check "$status_check" --argjson full_scans "$full_scans" --argjson cache_hits "$cache_hits" \
  --argjson full_interval "$COLLECT_INTERVAL" --argjson peer_interval "$PEER_INTERVAL" \
  --arg collect "$status_collect" --arg fetch "$status_fetch" --arg apply "$status_apply" \
  --arg error "$status_last_error" --arg error_at "$status_last_error_at" \
  --rawfile own "$own_file" --rawfile peer "$peer_file" \
  --rawfile requested "$request_file" --rawfile marker "$marker_file" --rawfile fleet "$fleet_file" '
   def decoded($text;$fallback): try ($text|fromjson) catch $fallback;
   def stamp: if .=="" then null else tonumber end;
   (decoded($own;[])|length) as $own_count |
   (decoded($peer;{})|(.reports//[])|length) as $peer_count |
   (decoded($requested;{})|(.list//[])|length) as $requested_count |
   (try ($marker|fromjson|if type=="number" and floor==. and .>=0 then . else null end) catch null) as $good |
   (if $good!=null and $tick>=$good then $tick-$good else null end) as $age |
   {version:3,profile:$profile,mode:$mode,pid:($pid|tonumber),sampled_uptime:$tick,
    state:$state,reason:$reason,interval_seconds:$interval,
    cache:{full_scans:$full_scans,hits:$cache_hits,full_interval_seconds:$full_interval,peer_interval_seconds:$peer_interval},
    last_success:{check:($check|stamp),collect:($collect|stamp),fetch:($fetch|stamp),apply:($apply|stamp)},
    peer:{last_revision_uptime:$good,age_at_sample_seconds:$age,ttl_seconds:$ttl,
          fresh:($peer_count>0 and $age!=null and $age<$ttl),fetch_result:$fetch_result},
    counts:{own:$own_count,cached_peer:$peer_count,requested_peer:$requested_count},
    last_error:(if $error=="" then null else {reason:$error,uptime:($error_at|stamp)} end)} +
    (if $fleet!="" then {peer_mode:"fleet",peers:decoded($fleet;[])} else {} end)' \
   > "$STATE/status.next" || return 1
 mv "$STATE/status.next" "$STATE/status.json" || return 1
 transition=$cycle_state:$cycle_reason
 if [ "$transition" != "$status_previous" ]; then
  note "state=$cycle_state reason=$cycle_reason"
  status_previous=$transition
 fi
}

read_status() {
 local tick
 # This path is intentionally before tools(), directory creation and the lock.
 [ ! -L "$BASE_STATE" ] && [ ! -L "$STATE" ] && [ ! -L "$STATE/status.json" ] && \
  [ -r "$STATE/status.json" ] || {
   printf '{"version":3,"profile":"%s","state":"unavailable","reason":"no_status"}\n' "$ROAM_PROFILE"
   return 1
  }
 tick=$(now) || return 1
 "$JQ" -e --arg profile "$ROAM_PROFILE" --argjson tick "$tick" '
  select(type=="object" and .version==3 and .profile==$profile and
    (.sampled_uptime|type=="number") and (.interval_seconds|type=="number")) |
  .sample_state=.state | .sample_reason=.reason |
  .snapshot_age_seconds=($tick-.sampled_uptime) |
  .snapshot_fresh=(.snapshot_age_seconds>=0 and .snapshot_age_seconds<=(3*.interval_seconds)) |
  .peer.age_seconds=(if .peer.last_revision_uptime!=null and $tick>=.peer.last_revision_uptime
    then $tick-.peer.last_revision_uptime else null end) |
  .peer.fresh=(.snapshot_fresh and .state!="stopped" and .counts.cached_peer>0 and
    .peer.age_seconds!=null and .peer.age_seconds<.peer.ttl_seconds) |
  . as $view |
  if has("peers") then .peers |= map(
    .age_seconds=(if .last_revision_uptime>=0 and $tick>=.last_revision_uptime then $tick-.last_revision_uptime else null end) |
    .fresh=($view.snapshot_fresh and $view.state!="stopped" and .age_seconds!=null and .age_seconds<$view.peer.ttl_seconds and .cached_count>0)) else . end |
  if .snapshot_fresh then . else .state="stale" | .reason="status_stale" end' "$STATE/status.json"
}

stop_daemon() {
 # A second signal exits rather than recursively entering cleanup.
 trap 'exit 0' INT TERM
 if [ -n "$roam_sleep_pid" ]; then
  kill "$roam_sleep_pid" 2>/dev/null || true
  wait "$roam_sleep_pid" 2>/dev/null || true
  roam_sleep_pid=""
 fi
 cycle_state=stopped; cycle_reason=stopped
 clear_tables || cycle_reason=stop_clear_failed
 write_status || true
 exit 0
}

cycle() {
 local tick full=0 changed=0 probe_ok=0 topology_changed=0
 cycle_state=ok; cycle_reason=ok
 tick=$(now)
 if [ "${mode:-once}" = daemon ]; then
  if ! config_signature; then
   clear_tables || true; cycle_state=error; cycle_reason=config_signature_failed; return 1
  fi
  if [ "$config_ready" != 1 ] || ! cmp -s "$STATE/config.signature.next" "$STATE/config.signature"; then changed=1; fi
 else changed=1
 fi
 if [ "$changed" = 1 ]; then
  if ! load_config; then
   clear_tables || true; cycle_state=error; cycle_reason=config_invalid; return 1
  fi
  config_ready=1; full=1; cached_source_ip=""; peer_due=0
  [ "${mode:-once}" != daemon ] || mv "$STATE/config.signature.next" "$STATE/config.signature" || return 1
 fi
 if [ "${mode:-once}" != daemon ]; then full=1
 else
  topology_probe "$STATE/topology.before" && probe_ok=1
  cmp -s "$STATE/topology.before" "$STATE/topology.signature" || topology_changed=1
  if [ "$probe_ok" != 1 ] || [ "$tick" -ge "$full_due" ] || \
     [ "$topology_changed" = 1 ]; then full=1; fi
 fi
 if [ "$full" = 1 ]; then
  if [ -r "$STATE/links.json" ]; then cp "$STATE/links.json" "$STATE/links.previous" || return 1
  else rm -f "$STATE/links.previous"
  fi
  if ! collect; then
   clear_tables || true; cycle_state=error; cycle_reason=collect_unavailable; return 1
  fi
  if [ "${mode:-once}" = daemon ]; then
   topology_probe "$STATE/topology.after" || true
   if ! cmp -s "$STATE/topology.before" "$STATE/topology.after"; then
    clear_tables || true; cycle_state=error; cycle_reason=topology_unstable; return 1
   fi
   mv "$STATE/topology.after" "$STATE/topology.signature" || return 1
  fi
  # A driver-side BSSID change must not turn a previously accepted peer into
  # our own candidate. Discard both legacy and fleet caches before refetching.
  if [ -r "$STATE/links.previous" ] && ! "$JQ" -e --slurpfile old "$STATE/links.previous" \
     '([.[].bssid]|sort)==([$old[0][].bssid]|sort)' "$STATE/links.json" >/dev/null; then
   rm -f "$STATE/peer.json" "$STATE/peer.last-good" "$STATE/peer.request.json"
   rm -rf "$STATE/peers"
   rm -f "$STATE/fleet.requests.applied" "$STATE/fleet.status.json"
   peer_due=0; force_apply=1
  fi
  status_collect=$(now); full_due=$((tick+COLLECT_INTERVAL)); full_scans=$((full_scans+1))
  publish_ready=0; cached_source_ip=""
  # Invalidate an applied generation on topology/config change; an ordinary
  # full audit instead compares the actual per-link databases below.
  if [ "$changed" = 1 ] || [ "$topology_changed" = 1 ] || [ "$probe_ok" != 1 ] || [ ! -r "$STATE/applied.request.json" ]; then force_apply=1; fi
 else cache_hits=$((cache_hits+1))
 fi
 status_check=$(now)
 publish || { clear_tables || true; cycle_state=error; cycle_reason=publish_failed; return 1; }
 if [ "${mode:-once}" != daemon ] || [ "$tick" -ge "$peer_due" ]; then
  if fetch_peer; then status_fetch=$(now); peer_due=$((tick+PEER_INTERVAL))
  else cycle_state=degraded; cycle_reason=$fetch_reason; peer_due=$((tick+roam_interval))
  fi
 elif [ "$fetch_reason" != ok ]; then cycle_state=degraded; cycle_reason=$fetch_reason
 fi
 request_list || { cycle_state=error; cycle_reason=request_failed; return 1; }
 if [ "$full" = 1 ] && ! audit_matches; then force_apply=1; fi
 if [ "${mode:-once}" != daemon ] || [ "$force_apply" = 1 ] || \
    ! cmp -s "$STATE/request.json" "$STATE/applied.request.json"; then
  apply_list || { cycle_state=error; cycle_reason=$apply_reason; return 1; }
  cp "$STATE/request.json" "$STATE/applied.request.json" || return 1
  force_apply=0; status_apply=$(now)
 fi
}

run_cycle() {
 local rc=0
 cycle || rc=$?
 if write_status; then status_write_failed=0
 elif [ "$status_write_failed" = 0 ]; then note status_write_failed; status_write_failed=1
 fi
 return "$rc"
}

main() {
 local mode=${1:-} start elapsed delay
 [ "$#" -le 2 ] || return 2
 select_profile "${2:-main}" || return 2
 [ "$mode" != status ] || { read_status; return; }
 tools || { note "required C7 tools missing"; return 1; }
 private_dirs
 exec 9>"$STATE/lock"
 flock -n 9 || { note "another helper owns state"; return 1; }
 case "$mode" in
  inspect)
   load_config || return 1
   collect || return 1
   "$JQ" -c 'map({bssid,ssid,nr,link_id})' "$STATE/links.json"
   ;;
  clear)
   if clear_tables; then
    cycle_state=stopped; cycle_reason=cleared
   else
    cycle_state=error; cycle_reason=clear_failed
    write_status || true
    return 1
   fi
   write_status
   ;;
  preflight)
   load_config
   [ -L "$WEB" ] || return 1
   [ "$(readlink "$WEB")" = "$BASE_PUBLIC" ] || return 1
   printf '%s\n' "C7 tools/config/public link ready"
   ;;
  once|daemon)
   [ -L "$WEB" ] && [ "$(readlink "$WEB")" = "$BASE_PUBLIC" ] || return 1
   if [ "$mode" = once ]; then run_cycle; return; fi
   rm -f "$STATE/peer.json" "$STATE/peer.last-good"
   rm -rf "$STATE/peers"
   rm -f "$STATE/fleet.requests.applied" "$STATE/fleet.status.json"
   trap stop_daemon INT TERM
   while :; do
    start=$(now); roam_interval=${roam_interval:-30}
    run_cycle || true
    elapsed=$(($(now) - start)); delay=$((roam_interval - elapsed))
    [ "$delay" -ge 1 ] || delay=1
    # Only the daemon owns FD9. An orphaned sleeper must not block restart.
    sleep "$delay" 9>&- & roam_sleep_pid=$!
    wait "$roam_sleep_pid" || true
    roam_sleep_pid=""
   done
   ;;
  *) printf '%s\n' "usage: helper.sh status|inspect|preflight|once|daemon|clear [main|battalion|guest|iot]" >&2; return 2;;
 esac
}
main "$@"
