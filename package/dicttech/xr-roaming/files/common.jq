# C7 jq has no ONIGURUMA: validation below is deliberately regex-free.
def chars_in($alphabet):
  if type != "string" then false else
    explode | all(.[]; . as $c | $alphabet | explode | index($c) != null)
  end;
def hex: chars_in("0123456789abcdef");
def profile_meta:
  {main:{iface:"ap-mld1",mld:true,max_links:3},
   battalion:{iface:"ap-mld0",mld:true,max_links:2},
   guest:{iface:"ap-mld2",mld:true,max_links:2},
   iot:{iface:"phy0.0-ap1",mld:false,max_links:1}};
def profile_ok: . as $p | ["main","battalion","guest","iot"] | index($p)!=null;
def section_token:
  type=="string" and length>=1 and length<=64 and
  chars_in("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_");
def byte_hex:
  . as $n | "0123456789abcdef"[($n/16|floor):(($n/16|floor)+1)] +
  "0123456789abcdef"[($n%16):(($n%16)+1)];
def ssid_bytes:
  [explode[] | . as $c |
   if $c<128 then $c
   elif $c<2048 then (192+($c/64|floor)),(128+($c%64))
   elif $c<65536 then (224+($c/4096|floor)),(128+(($c/64|floor)%64)),(128+($c%64))
   else (240+($c/262144|floor)),(128+(($c/4096|floor)%64)),(128+(($c/64|floor)%64)),(128+($c%64)) end];
def ssid_hex: ssid_bytes | map(byte_hex) | join("");
def ssid_status:
  ssid_bytes | map(if .==34 then "\\\"" elif .==92 then "\\\\"
    elif .>=32 and .<=126 then [.]|implode else "\\x"+byte_hex end) | join("");
def ssid_ok:
  if type!="string" then false else
    ssid_bytes | length>=1 and length<=32 and all(.[];.>=32 and .!=127)
  end;
def uint_string($max):
  if type != "string" then false else
    length >= 1 and length <= 10 and chars_in("0123456789") and
    (length == 1 or (startswith("0") | not)) and (tonumber <= $max)
  end;
def mac:
  if type != "string" then false else
    . as $m | split(":") as $p |
    length == 17 and ($p|length)==6 and
    ($p|all(.[]; length==2 and hex)) and
    ("02468ace" | index($p[0][1:2]) != null) and $m != "00:00:00:00:00:00"
  end;
def uuid:
  if type != "string" then false else
    split("-") as $p | ($p|map(length))==[8,4,4,4,12] and ($p|all(.[];hex))
  end;
def hexbyte:
  explode | map(if . >= 48 and . <= 57 then . - 48 else . - 87 end) |
  .[0] * 16 + .[1];
def nr_tlvs:
  if length == 0 then true elif length < 4 then false else
    . as $s | (.[2:4] | hexbyte) as $n |
    if length < (4 + 2 * $n) then false else $s[(4 + 2 * $n):] | nr_tlvs end
  end;
def nr($bssid):
  if type != "string" then false else
    length >= 26 and length <= 510 and (length % 2 == 0) and hex and
    startswith($bssid | split(":") | join("")) and
    (.[20:22] | hexbyte > 0) and (.[22:24] | hexbyte > 0) and
    (.[26:] | nr_tlvs)
  end;
def report_ok($ssid):
  if type != "object" then false else
    . as $r | (keys | sort) == ["bssid", "nr", "ssid"] and
    .ssid == $ssid and (.bssid | mac) and (.nr | nr($r.bssid))
  end;
def private_ipv4:
  if type != "string" then false else
    split(".") as $p |
    if ($p|length)!=4 or ($p|all(.[];uint_string(255))|not) then false else
      ($p | map(tonumber)) as $a |
      ($a[3] > 0 and $a[3] < 255) and
      ($a[0] == 10 or ($a[0] == 172 and $a[1] >= 16 and $a[1] <= 31) or
       ($a[0] == 192 and $a[1] == 168))
    end
  end;
def config_ok:
  . as $c | type == "object" and .enabled == "1" and (.profile|profile_ok) and (.ssid|ssid_ok) and
  .iface==(profile_meta[$c.profile].iface) and .mld==(profile_meta[$c.profile].mld) and
  .max_links==(profile_meta[$c.profile].max_links) and
  ((.section|section_token) or (.profile=="main" and .section=="")) and
  (.peer | private_ipv4) and (.self_mac | mac) and (.peer_mac | mac) and
  .self_mac != .peer_mac and
  (.peer_bssids | type == "array" and length >= 1 and length <= $c.max_links and
   all(.[]; mac) and (unique | length) == length) and
  (.interval | type == "number" and floor == . and . >= 30 and . <= 300) and
  (.ttl | type == "number" and floor == . and . >= 180 and . <= 1800) and
  .ttl >= (3 * .interval);
def public_ok($ssid; $peer_mac; $allowed):
  type == "object" and (keys | sort) == ["bridge_mac", "reports", "revision", "ssid", "version"] and
  .version == 1 and .ssid == $ssid and .bridge_mac == $peer_mac and
  (.revision | uuid) and
  (.reports | type == "array" and length >= 1 and length <= 3 and
   all(.[]; report_ok($ssid) and (.bssid as $m | $allowed | index($m) != null)) and
   ([.[].bssid] | unique | length) == length);
