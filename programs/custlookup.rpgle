**free
// CUSTLOOKUP.RPGLE -- a program meant to be CALLed. Give it a customer
// number and it fills in the name and balance, and says whether it
// found one. The dcl-pi is its parameter list: what a caller passes.
//
// It sets *inlr on, so it closes its file and starts fresh every call.
// Leave *inlr off and it would stay active between calls, keeping its
// file position, which is how RPG subprograms keep state.

dcl-pi *n;
  id    packed(5) const;
  nm    char(20);
  owed  packed(9:2);
  found ind;
end-pi;

dcl-f custmast keyed;

chain id custmast;
found = %found(custmast);
if found;
  nm = name;
  owed = bal;
else;
  nm = ' ';
  owed = 0;
endif;

*inlr = *on;
return;
