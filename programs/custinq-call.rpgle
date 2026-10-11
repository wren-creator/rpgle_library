**free
// CUSTINQ-CALL.RPGLE -- the customer inquiry again, but this program
// owns only the screen. The lookup lives in custlookup.rpgle, which it
// calls by name: rpgle looks for custlookup.rpgle in this program's
// folder (then in any -I folder).
//
//   rpgle run programs/custinq-call.rpgle --dspf programs/custinq.dspf --db shop.db

dcl-f custinq workstn;

dcl-pr custlookup extpgm;
  id    packed(5) const;
  nm    char(20);
  owed  packed(9:2);
  found ind;
end-pr;

dcl-s ok ind;

exfmt inq;
dow not *in03;
  *in90 = *off;
  custlookup(custno : name : bal : ok);
  if not ok;
    *in90 = *on;
    msgtxt = 'Customer not found';
  endif;
  exfmt inq;
enddo;

*inlr = *on;
return;
