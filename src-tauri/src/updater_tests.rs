use serde_json::json;
use std::{
    io::{Read, Write},
    net::TcpListener,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};
use tauri_plugin_updater::UpdaterExt;

// Test-only public vectors from the official updater; no private key is present.
const PUBLIC_KEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEE0OUVGNDc0NkZBNkU2RDcKUldUWDVxWnZkUFNlcERHV2cyYno4bjZBclFuQk13Z1FLMWdlVm9zb3JjYkx6Y3VuRCtLSitjWWU=";
const SIGNATURE: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVUWDVxWnZkUFNlcFBGMnpzanZQL0wvVFF0QkxSbldSdmFxKzRvbUsxVUV1Z3RRcC9VU3JHaUkxdjc2MnNEeTNpZDNhb3I3YlJDOUVUeWVvdDZsTUhIcldvWjVoalVNMVFjPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzkxNjQ5MDUxCWZpbGU6cGF5bG9hZAl2ZXJzaW9uOjIuMC4wCkREcTJDTE9FbDVjUW1DMmVKUkhsblpQVlZpT1pzdTFDanBQS1FvZWk0QS9YQmZrRHFHTzBCK1FNcmNySGdFUTVxVjNtbmU3NXRkRldoSnBxVVhoOUFBPT0=";
struct Server {
    url: String,
    stop: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Server {
    fn new(version: &str, payload: &[u8], no_content: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let manifest = json!({"version":version,"notes":"test release","platforms":{"test-target":{"url":format!("{url}/payload"),"signature":SIGNATURE}}}).to_string().into_bytes();
        let payload = payload.to_vec();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = stop.clone();
        let worker = thread::spawn(move || {
            while !flag.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        stream
                            .set_read_timeout(Some(Duration::from_secs(2)))
                            .unwrap();
                        stream
                            .set_write_timeout(Some(Duration::from_secs(2)))
                            .unwrap();
                        let mut request = [0; 4096];
                        let count = stream.read(&mut request).unwrap_or(0);
                        let is_payload =
                            String::from_utf8_lossy(&request[..count]).starts_with("GET /payload ");
                        let body = if is_payload { &payload } else { &manifest };
                        let (status, body): (&str, &[u8]) = if no_content && !is_payload {
                            ("204 No Content", &[])
                        } else {
                            ("200 OK", body)
                        };
                        let _ = write!(stream,"HTTP/1.1 {status}\r\nContent-Length: {}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n",body.len());
                        let _ = stream.write_all(body);
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5))
                    }
                    Err(error) => panic!("server failed: {error}"),
                }
            }
        });
        Self {
            url,
            stop,
            worker: Some(worker),
        }
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        self.worker.take().unwrap().join().unwrap();
    }
}
fn app() -> tauri::App<tauri::test::MockRuntime> {
    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    context.config_mut().plugins.0.insert(
        "updater".into(),
        json!({"pubkey":PUBLIC_KEY,"dangerousInsecureTransportProtocol":true}),
    );
    tauri::test::mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(context)
        .unwrap()
}
#[test]
fn official_updater_ignores_no_update_equal_and_older_versions() {
    let app = app();
    tauri::async_runtime::block_on(async {
        for (version, no_content) in [("2.0.0", true), ("0.1.0", false), ("0.0.1", false)] {
            let server = Server::new(version, b"test", no_content);
            let updater = app
                .updater_builder()
                .target("test-target")
                .no_proxy()
                .timeout(Duration::from_secs(3))
                .endpoints(vec![format!("{}/latest.json", server.url).parse().unwrap()])
                .unwrap()
                .build()
                .unwrap();
            assert!(updater.check().await.unwrap().is_none());
        }
    });
}
#[test]
fn official_updater_accepts_signed_payload_and_rejects_tampering() {
    let app = app();
    tauri::async_runtime::block_on(async {
        for (payload, valid) in [(b"test".as_slice(), true), (b"tampered".as_slice(), false)] {
            let server = Server::new("2.0.0", payload, false);
            let updater = app
                .updater_builder()
                .target("test-target")
                .no_proxy()
                .timeout(Duration::from_secs(3))
                .endpoints(vec![format!("{}/latest.json", server.url).parse().unwrap()])
                .unwrap()
                .build()
                .unwrap();
            let update = updater.check().await.unwrap().expect("newer release");
            assert_eq!(update.version, "2.0.0");
            let download = update.download(|_, _| {}, || {}).await;
            if valid {
                assert_eq!(download.unwrap(), b"test");
            } else {
                assert!(download.is_err(), "tampered package accepted");
            }
        }
    });
}
