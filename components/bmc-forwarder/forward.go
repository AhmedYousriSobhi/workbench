// A TCP forwarder: `forward 443:bmc-farm:9000 8443:bmc-farm:9000` listens on each port and pipes the bytes to the target.
// One runs per node BMC (ADR 0018, issue 454): the container's own IP is the BMC's address, the farm keeps all the state.
// It never looks at the bytes, so TLS ends at the farm and the same certificate is presented on every address.
// Static binary on scratch: a few MB where the Python forwarder (a Python asyncio script, removed in issue 454) used about 18.
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"strings"
	"sync"
)

// pipe copies until the source ends, then half-closes the destination so the other direction can finish.
func pipe(dst, src net.Conn, wg *sync.WaitGroup) {
	defer wg.Done()
	io.Copy(dst, src)
	if t, ok := dst.(*net.TCPConn); ok {
		t.CloseWrite()
	}
}

func handle(c net.Conn, target string) {
	defer c.Close()
	up, err := net.Dial("tcp", target)
	if err != nil { // a dead target just closes, as the Python forwarder did
		return
	}
	defer up.Close()
	var wg sync.WaitGroup
	wg.Add(2)
	go pipe(up, c, &wg)
	go pipe(c, up, &wg)
	wg.Wait()
}

func serve(l net.Listener, target string) {
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		go handle(c, target)
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: forward LISTEN_PORT:HOST:PORT ...")
		os.Exit(2)
	}
	done := make(chan struct{})
	for _, spec := range os.Args[1:] {
		parts := strings.Split(spec, ":")
		if len(parts) != 3 {
			fmt.Fprintf(os.Stderr, "bad spec %q: want LISTEN_PORT:HOST:PORT\n", spec)
			os.Exit(2)
		}
		l, err := net.Listen("tcp", "0.0.0.0:"+parts[0])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		go serve(l, net.JoinHostPort(parts[1], parts[2]))
	}
	<-done
}
