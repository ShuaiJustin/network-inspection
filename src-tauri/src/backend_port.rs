use std::io;
use std::net::TcpListener;

pub struct BackendPortReservation {
    listener: TcpListener,
    port: u16,
}

impl BackendPortReservation {
    pub fn reserve() -> io::Result<Self> {
        let listener = TcpListener::bind(("127.0.0.1", 0))?;
        let port = listener.local_addr()?.port();
        Ok(Self { listener, port })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn release(self) -> u16 {
        let port = self.port;
        drop(self.listener);
        port
    }
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use super::BackendPortReservation;

    #[test]
    fn reserved_backend_port_cannot_be_claimed() {
        let reservation = BackendPortReservation::reserve().expect("an available local port");

        assert!(TcpListener::bind(("127.0.0.1", reservation.port())).is_err());
    }

    #[test]
    fn released_backend_port_becomes_bindable() {
        let reservation = BackendPortReservation::reserve().expect("an available local port");
        let port = reservation.release();

        TcpListener::bind(("127.0.0.1", port)).expect("released port is bindable");
    }
}
