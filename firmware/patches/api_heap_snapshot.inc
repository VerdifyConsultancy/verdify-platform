  // Allocation-free main-loop snapshot: no socket/lock calls or state changes.
  // Excludes allocator metadata, lwIP/WiFi and Noise crypto allocations.
  struct HeapSnapshot {
    size_t peers{0}, removing{0}, syncing{0}, overflow_entries{0};
    size_t overflow_bytes{0}, pending_bytes{0}, batch_items{0};
    size_t batch_bytes{0}, rx_bytes{0}, shared_bytes{0};
  };
  HeapSnapshot heap_snapshot() const {
    HeapSnapshot out;
    out.shared_bytes = this->shared_write_buffer_.capacity_bytes();
    for (const auto &client : this->active_clients()) {
      ++out.peers;
      out.removing += client->flags_.remove;
      out.syncing += client->active_iterator_ != APIConnection::ActiveIterator::NONE;
      out.batch_items += client->deferred_batch_.size();
      out.batch_bytes += client->deferred_batch_.capacity * sizeof(APIConnection::DeferredBatch::BatchItem);
      if (client->helper_ != nullptr) {
        out.overflow_entries += client->helper_->overflow_count();
        out.overflow_bytes += client->helper_->overflow_allocated_bytes();
        out.pending_bytes += client->helper_->overflow_pending_bytes();
        out.rx_bytes += client->helper_->rx_capacity_bytes();
      }
    }
    return out;
  }
