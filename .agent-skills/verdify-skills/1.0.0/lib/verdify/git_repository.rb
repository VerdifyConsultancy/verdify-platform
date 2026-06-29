# frozen_string_literal: true

module Verdify
  class GitRepository
    attr_reader :root

    def initialize(path)
      requested = Pathname.new(path).expand_path
      stdout, = capture("git", "-C", requested.to_s, "rev-parse", "--show-toplevel")
      @root = Pathname.new(stdout.strip).expand_path
    rescue CommandError
      raise UsageError, "Not a Git repository: #{requested}"
    end

    def capture(*command, allow_failure: false)
      stdout, stderr, status = Open3.capture3(*command)
      unless status.success? || allow_failure
        raise CommandError, "Command failed (#{command.shelljoin}): #{stderr.strip.empty? ? stdout.strip : stderr.strip}"
      end
      [stdout, stderr, status]
    end

    def output(*command)
      capture(*command).first.strip
    end

    def git(*args, allow_failure: false)
      capture("git", "-C", root.to_s, *args, allow_failure: allow_failure)
    end

    def head_sha(ref = "HEAD")
      git("rev-parse", "#{ref}^{commit}").first.strip
    end

    def current_branch
      git("branch", "--show-current").first.strip
    end

    def default_branch
      remote_head = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", allow_failure: true)
      if remote_head.last.success?
        return remote_head.first.strip.sub(%r{\Aorigin/}, "")
      end
      %w[main master].find { |name| branch_exists?(name) } || current_branch
    end

    def clean?(path = root)
      stdout, = capture("git", "-C", path.to_s, "status", "--porcelain")
      stdout.strip.empty?
    end

    def branch_exists?(branch)
      git("show-ref", "--verify", "--quiet", "refs/heads/#{branch}", allow_failure: true).last.success?
    end

    def common_dir
      raw = git("rev-parse", "--git-common-dir").first.strip
      path = Pathname.new(raw)
      path.absolute? ? path : root.join(path).cleanpath
    end

    def remote_url
      result = git("remote", "get-url", "origin", allow_failure: true)
      result.last.success? ? result.first.strip : nil
    end

    def github_slug
      url = remote_url
      return nil if url.nil? || url.empty?
      match = url.match(%r{github\.com[:/](?<slug>[^/]+/[^/]+?)(?:\.git)?\z})
      match && match[:slug]
    end

    def add_worktree(path:, branch:, base:, detach: false)
      FileUtils.mkdir_p(Pathname.new(path).dirname)
      if detach
        git("worktree", "add", "--detach", path.to_s, base)
      elsif branch_exists?(branch)
        git("worktree", "add", path.to_s, branch)
      else
        git("worktree", "add", "-b", branch, path.to_s, base)
      end
    end

    def lock_worktree(path, reason)
      result = git("worktree", "lock", "--reason", reason, path.to_s, allow_failure: true)
      return if result.last.success?

      fallback = git("worktree", "lock", path.to_s, allow_failure: true)
      raise CommandError, "Could not lock worktree #{path}: #{fallback[1].strip}" unless fallback.last.success?
    end

    def unlock_worktree(path)
      git("worktree", "unlock", path.to_s, allow_failure: true)
    end

    def remove_worktree(path, force: false)
      args = ["worktree", "remove"]
      args << "--force" if force
      args << path.to_s
      git(*args)
    end

    def worktrees
      stdout = git("worktree", "list", "--porcelain").first
      records = []
      current = {}
      stdout.each_line do |line|
        line = line.chomp
        if line.empty?
          records << current unless current.empty?
          current = {}
          next
        end
        key, value = line.split(" ", 2)
        current[key] = value || true
      end
      records << current unless current.empty?
      records
    end
  end
end
