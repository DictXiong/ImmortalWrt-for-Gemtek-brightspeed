include "common";
def one: if length == 1 then .[0] else error("expected exactly one local entry") end;
def status_map:
  split("\n") | map(select(contains("=")) | split("=") as $p |
    {key:$p[0],value:($p[1:]|join("="))}) | from_entries;
def socket_parts($iface;$mld):
  split("_link") as $p |
  if $mld==false and .==$iface then {base:$iface,link:null,mld:false}
  elif $mld==true and ($p|length)==2 and ($p[0]|length)>=1 and ($p[0]|length)<=32 and
     ($p[0]|chars_in("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")) and
     ($p[1]|uint_string(14)) and $p[0]==$iface then {base:$p[0],link:$p[1],mld:true}
  else error("invalid link socket") end;
def neighbor_row:
  split(" ") | map(select(length>0)) as $p |
  select(($p|length)>=3 and ($p[0]|ascii_downcase|mac) and
    ($p[1]|startswith("ssid=")) and ($p[2]|startswith("nr="))) |
  {bssid:($p[0]|ascii_downcase),ssidhex:($p[1][5:]|ascii_downcase),
   nr:($p[2][3:]|ascii_downcase)};
# The fallback serves old standalone parser callers only; every helper call
# supplies its validated configured iface explicitly.
($ARGS.named.iface // "ap-mld1") as $iface |
(if $ARGS.named|has("mld") then $ARGS.named.mld else true end) as $mld |
($socket | socket_parts($iface;$mld)) as $s |
($status | status_map) as $st |
if $st.state != "ENABLED" then error("link is not enabled") else . end |
($st | to_entries | map(select(.key|startswith("bss[") and endswith("]")) |
 . as $kv | .key[4:-1] as $i | select($i|uint_string(1024)) |
 select(($kv.value == $s.base or $kv.value == $socket) and
        $st["ssid[" + $i + "]"] == ($ssid|ssid_status) and
        (if $s.mld then $st["mld_link_id[" + $i + "]"] == $s.link
         else $st["mld_link_id[" + $i + "]"]==null and $st["mld_addr["+$i+"]"]==null end)) |
 {bssid: ($st["bssid[" + $i + "]"] | ascii_downcase), base: $s.base,
  link_id: (if $s.mld then $s.link|tonumber else null end), mld:$s.mld, index:$i} ) | one) as $own |
if ($own.bssid | mac) then . else error("invalid STATUS BSSID") end |
if $ARGS.named.mode == "status" then ($own|del(.index)) else
($ARGS.named.status_after // $status | status_map) as $after |
(["state","freq","channel","secondary_channel","vht_oper_chwidth",
  "he_oper_chwidth","eht_oper_chwidth","vht_oper_centr_freq_seg0_idx",
  "he_oper_centr_freq_seg0_idx","eht_oper_centr_freq_seg0_idx",
  "bssid["+$own.index+"]","ssid["+$own.index+"]","mld_link_id["+$own.index+"]"]) as $stable |
if all($stable[]; . as $k | $st[$k] == $after[$k]) then .
else error("link changed during collection") end |
($neighbors | split("\n") | map(neighbor_row |
 select(.bssid == $own.bssid and .ssidhex == $ssidhex)) | one) as $r |
{bssid:$own.bssid, ssid:$ssid, nr:$r.nr} as $report |
if ($report | report_ok($ssid)) and ($r.nr[22:24] | hexbyte) == ($st.channel | tonumber) then
  $report + {base:$own.base, link_id:$own.link_id,mld:$own.mld} +
  (if $ARGS.named.observed==true then
    {observed:($neighbors|split("\n")|map(neighbor_row|select(.bssid!=$own.bssid))|sort_by(.bssid))}
   else {} end)
else error("invalid own NR") end end
