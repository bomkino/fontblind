FontBlind for Linux

Run ./FontBlind. FontBlind starts a private loopback-only server, opens your
default browser, and keeps every source and output on this machine.

The portable bundle does not install files outside its own directory. The
AppImage may be launched directly after chmod +x.

Set FONTBLIND_NO_OPEN=1 to print the local address without opening a browser.
Closing the launcher stops the local server and removes its private launch log.
