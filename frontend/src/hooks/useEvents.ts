import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useUi } from "../stores/ui";

export function useEventStream() {
  const token = useUi((state) => state.token);
  const setLive = useUi((state) => state.setLive);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!token) return;
    let socket: EventSource | null = null;
    let delay = 1000;
    let stopped = false;
    let timer = 0;

    const connect = () => {
      if (stopped) return;
      socket = new EventSource("/api/v1/events/stream", { withCredentials: true });
      socket.onopen = () => {
        delay = 1000;
        setLive(true);
      };
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.type?.startsWith("incident") || payload.type?.startsWith("alert") || payload.type?.startsWith("scenario") || payload.type === "metric_updated" || payload.type === "new_log_batch") {
          queryClient.invalidateQueries();
        }
      };
      socket.onerror = () => {
        setLive(false);
        socket?.close();
        timer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 15000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
      setLive(false);
    };
  }, [token, queryClient, setLive]);
}
