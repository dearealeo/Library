class MihomoATalpha < Formula
  desc "Rule-based tunnel in Go (Alpha prerelease of MetaCubeX/mihomo)"
  homepage "https://wiki.metacubex.one"
  url "https://github.com/MetaCubeX/mihomo/archive/refs/tags/Prerelease-Alpha.tar.gz"
  sha256 "43781f8ee1662ebb68e06884ac635f264cea5d942d13e72521f580cc183c7b2e"
  license "GPL-3.0-or-later"
  head "https://github.com/MetaCubeX/mihomo.git", branch: "Alpha"

  depends_on "go" => :build

  def install
    ENV["CGO_ENABLED"] = "0"
    ENV["GOOS"] = OS.kernel_name.downcase
    ENV["GOARCH"] = Hardware::CPU.arch.to_s

    ldflags = "-s -w -buildid= -X github.com/metacubex/mihomo/constant.Version=#{version} -X github.com/metacubex/mihomo/constant.BuildTime=#{time.iso8601}"

    system "go", "build", "-ldflags", ldflags, "-tags", "with_gvisor", "-trimpath", "-o", bin/"mihomo"

    clashx_cfg = "#{Dir.home}/Library/Mobile Documents/iCloud~com~metacubex~ClashX/Documents/config.yaml"
    cfg = File.exist?(clashx_cfg) ? File.read(clashx_cfg) : "mixed-port: 7890\n"

    (pkgetc/"config.yaml").write(cfg)
  end

  def caveats
    "Configuration sourced from iCloud if available, otherwise defaults applied. Customize #{etc}/mihomo/config.yaml as required."
  end

  service do
    run [opt_bin/"mihomo", "-d", etc/"mihomo"]
    keep_alive true
    working_dir etc/"mihomo"
    log_path var/"log/mihomo.log"
    error_log_path var/"log/mihomo.log"
  end

  service do
    run [opt_bin/"mihomo", "-d", etc/"mihomo"]
    keep_alive true
    working_dir etc/"mihomo"
    log_path var/"log/mihomo.log"
    error_log_path var/"log/mihomo.log"
    process_type :background
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/mihomo -v")
    (testpath/"config.yaml").write("mixed-port: #{free_port}\n")
    system bin/"mihomo", "-t", "-f", testpath/"config.yaml"
  end
end
