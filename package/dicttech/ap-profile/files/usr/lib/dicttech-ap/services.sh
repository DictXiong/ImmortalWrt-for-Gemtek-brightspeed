#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Apply producer-owned service policy; factory enrollment has a separate policy.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077

MARKER=/etc/dicttech-ap/device.json
FACTORY=/usr/lib/dicttech-ap/factory/setup/services.json
CONTRACT=/usr/share/dicttech-ap/contract.json
ENGINE_SERVICES=$(jq -er '[.reserved_services[]|select(type=="string")]|select(length>=1)|join(" ")' "$CONTRACT")

allowed() {
 case "$1" in ''|*[!A-Za-z0-9_.+-]*|.*) return 1;; *) return 0;; esac
}
service_names() {
 local file name
 for file in /etc/init.d/*; do
  [ -f "$file" ] || continue
  name=${file##*/}; allowed "$name" || return 1
  case " $ENGINE_SERVICES " in *" $name "*) continue;; esac
  printf '%s\n' "$name"
 done
}
source_policy() {
 if [ "${1:-managed}" = setup ] || [ ! -f "$MARKER" ]; then printf '%s\n' "$FACTORY"
 else printf '%s\n' "$MARKER"; fi
}
validate_policy() {
 jq -e --arg engines "$ENGINE_SERVICES" '
  .services|type=="object" and length<=128 and all(to_entries[];
   (.key as $name|($engines|split(" ")|index($name))==null) and
   (.key|type=="string" and length>=1 and length<=255 and ((explode) as $c | $c[0]!=45 and $c[0]!=46 and all($c[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95 or .==43 or .==45 or .==46))) and
   (.value|type=="object" and (.enable|type=="boolean") and
    ((.running|type)=="boolean" or .running==null) and
    ((.oneshot // false)|type=="boolean") and
    (.enable or .running!=true)))
 ' "$1" >/dev/null
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
   *) printf 'dicttech-ap: unexpected service link: %s\n' "$name" >&2; return 1;; esac
  jq -cn --arg name "$name" --arg target "$target" '{name:$name,target:$target}' || return 1
 done
}
snapshot() {
 local dest=$1 tmp service enabled active links oneshot source names count=0
 case "$dest" in /*) ;; *) return 2;; esac
 [ ! -L "$dest" ] || return 1
 source=$(source_policy); validate_policy "$source" || return 1
 names=$(service_names) || return 1
 for service in $names; do count=$((count+1)); [ "$count" -le 128 ] || return 1; done
 tmp=$dest.$$.tmp
 [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || return 1
 : > "$tmp"
 for service in $names; do
  enabled=false
  /etc/init.d/"$service" enabled >/dev/null 2>&1 && enabled=true
  active=$(running_state "$service") || { rm -f "$tmp"; return 1; }
  oneshot=$(jq -r --arg name "$service" '.services[$name].oneshot // false' "$source") || return 1
  links=$(rc_links "$service") || { rm -f "$tmp"; return 1; }
  links=$(printf '%s\n' "$links" | jq -sc '.') || { rm -f "$tmp"; return 1; }
  jq -cn --arg service "$service" --argjson enabled "$enabled" --argjson running "$active" \
   --argjson oneshot "$oneshot" --argjson links "$links" \
   '{service:$service,present:true,enabled:$enabled,running:$running,oneshot:$oneshot,links:$links}' >> "$tmp" || { rm -f "$tmp"; return 1; }
 done
 [ ! -e "$tmp.json" ] && [ ! -L "$tmp.json" ] || { rm -f "$tmp"; return 1; }
 jq -sc '{schema_version:1,services:.}' "$tmp" > "$tmp.json" || return 1
 chmod 600 "$tmp.json" || return 1
 validate_snapshot "$tmp.json" || { rm -f "$tmp" "$tmp.json"; return 1; }
 mv "$tmp.json" "$dest" || return 1
 rm -f "$tmp"
}
policy() {
 local source
 source=$(source_policy "${1:-managed}") || return 1
 # Managed operation never falls back to factory policy after enrollment.
 [ "${1:-managed}" = setup ] || [ -f "$MARKER" ] || return 1
 validate_policy "$source" || return 1
 jq -r '.services|to_entries|sort_by(.key)[]|[.key,(.value.enable|tostring),(.value.running|tostring),((.value.oneshot // false)|tostring)]|@tsv' "$source"
}
apply_policy() {
 local phase=$1 rows service enabled desired oneshot active
 rows=$(policy "${2:-managed}") || return 1
 printf '%s\n' "$rows" | while IFS="$(printf '\t')" read -r service enabled desired oneshot; do
  [ -n "$service" ] || continue
  allowed "$service" || return 1
  [ -x "/etc/init.d/$service" ] || continue
  if [ "$enabled" = false ]; then
   /etc/init.d/"$service" disable || return 1
   [ "$phase" = links ] || /etc/init.d/"$service" stop || return 1
  else
   /etc/init.d/"$service" enable || return 1
   if [ "$phase" != links ]; then
    if [ "$oneshot" = true ]; then /etc/init.d/"$service" start || return 1
    elif [ "$desired" = true ]; then
     active=$(running_state "$service") || return 1
     [ "$active" = true ] || /etc/init.d/"$service" start || return 1
    elif [ "$desired" = false ]; then /etc/init.d/"$service" stop || return 1
    fi
   fi
  fi
 done
}
validate_snapshot() {
 local file=$1 service name target rows names actual
 [ -f "$file" ] && [ ! -L "$file" ] || return 1
 jq -e '.schema_version==1 and (.services|type=="array" and length<=128) and
   (.services|all(.[]; (.service|type=="string" and length>=1 and length<=255 and ((explode) as $c | $c[0]!=45 and $c[0]!=46 and all($c[]; (.>=48 and .<=57) or (.>=65 and .<=90) or (.>=97 and .<=122) or .==95 or .==43 or .==45 or .==46))) and
     .present==true and (.enabled|type=="boolean") and (.running|type=="boolean") and
     (.oneshot|type=="boolean") and (.links|type=="array"))) and
   ([.services[].service]|length==(unique|length))' "$file" >/dev/null || return 1
 names=$(jq -r '.services[].service' "$file" | sort) || return 1
 actual=$(service_names | sort) || return 1
 [ "$names" = "$actual" ] || return 1
 rows=$(jq -r '.services[] | .service as $s | .links[] | [$s,.name,.target] | @tsv' "$file") || return 1
 printf '%s\n' "$rows" |
 while IFS="$(printf '\t')" read -r service name target; do
  [ -n "$service" ] || continue
  case "$name" in S[0-9][0-9]"$service"|K[0-9][0-9]"$service") ;; *) return 1;; esac
  case "$target" in "../init.d/$service"|"/etc/init.d/$service") ;; *) return 1;; esac
 done
}
restore() {
 local file=$1 phase=$2 service enabled active oneshot actual link name target rows
 validate_snapshot "$file" || return 1
 case "$phase" in
  links)
   rows=$(jq -r '.services[].links[] | [.name,.target] | @tsv' "$file") || return 1
   for service in $(jq -r '.services[].service' "$file"); do
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
   rows=$(jq -r '.services[] | [.service,(.enabled|tostring),(.running|tostring),(.oneshot|tostring)] | @tsv' "$file") || return 1
   printf '%s\n' "$rows" |
   while IFS="$(printf '\t')" read -r service enabled active oneshot; do
    [ -x "/etc/init.d/$service" ] || continue
    if [ "$oneshot" = true ]; then
     [ "$enabled" != true ] || /etc/init.d/"$service" start || return 1
     continue
    fi
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
