"""
Scripts de teste para a API de Raster - AlienSmart
Execute: python test_raster_api.py
"""

import json
import requests
from pathlib import Path

BASE_URL = "http://localhost:8000"
RASTERS_DIR = Path(__file__).parent.parent / "Dados rasters"

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_raster_species():
    """Testa obtenção de espécies com dados raster."""
    print_section("1. TESTE: Listar Espécies com Dados Raster")
    
    try:
        resp = requests.get(f"{BASE_URL}/raster/species")
        resp.raise_for_status()
        data = resp.json()
        
        species_list = data.get("species", [])
        print(f"✅ Sucesso! {len(species_list)} espécies encontradas")
        print(f"Primeiras 5: {species_list[:5]}")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_raster_files():
    """Testa listagem de ficheiros raster com filtros."""
    print_section("2. TESTE: Listar Ficheiros Raster")
    
    tests = [
        ("Sem filtros", {}),
        ("Período histórico", {"period": "hist"}),
        ("Apenas binários", {"binary": True}),
    ]
    
    for name, params in tests:
        try:
            resp = requests.get(f"{BASE_URL}/raster/files", params=params)
            resp.raise_for_status()
            data = resp.json()
            count = data.get("count", 0)
            print(f"✅ {name}: {count} ficheiros encontrados")
        except Exception as e:
            print(f"❌ {name}: {e}")

def test_parse_filename():
    """Testa parsing de nome de ficheiro."""
    print_section("3. TESTE: Parser de Nome de Ficheiro")
    
    # Encontra um ficheiro real para testar
    if not RASTERS_DIR.exists():
        print(f"⚠️  Diretório de rasters não encontrado: {RASTERS_DIR}")
        return False
    
    raster_files = list(RASTERS_DIR.glob("*.tif"))
    if not raster_files:
        print(f"⚠️  Nenhum ficheiro .tif encontrado em {RASTERS_DIR}")
        return False
    
    sample_filename = raster_files[0].name
    print(f"Testando com: {sample_filename}\n")
    
    try:
        resp = requests.get(f"{BASE_URL}/raster/parse/{sample_filename}")
        resp.raise_for_status()
        data = resp.json()
        
        print(f"✅ Parser bem-sucedido:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_suitable_area():
    """Testa cálculo de área adequada."""
    print_section("4. TESTE: Área Adequada para Espécie")
    
    # Primeiro, obtém lista de espécies
    try:
        resp_species = requests.get(f"{BASE_URL}/raster/species")
        species_list = resp_species.json().get("species", [])
        
        if not species_list:
            print("⚠️  Nenhuma espécie encontrada")
            return False
        
        test_species = species_list[0]
        print(f"Testando com espécie: {test_species}\n")
        
        resp = requests.get(
            f"{BASE_URL}/raster/suitable-area/{test_species}",
            params={"period": "hist"}
        )
        resp.raise_for_status()
        data = resp.json()
        
        stats = data.get("data", {})
        print(f"✅ Estatísticas calculadas:")
        print(f"  • Área adequada: {stats.get('suitable_area_km2', 'N/A'):.2f} km²")
        print(f"  • Percentual: {stats.get('suitable_pct', 'N/A'):.2f}%")
        print(f"  • Resolução: {stats.get('pixel_res_m', 'N/A')}m")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_compare_periods():
    """Testa comparação entre períodos."""
    print_section("5. TESTE: Comparação Entre Períodos")
    
    try:
        resp_species = requests.get(f"{BASE_URL}/raster/species")
        species_list = resp_species.json().get("species", [])
        
        if not species_list:
            print("⚠️  Nenhuma espécie encontrada")
            return False
        
        test_species = species_list[0]
        print(f"Testando comparação para: {test_species}\n")
        
        resp = requests.get(
            f"{BASE_URL}/raster/compare-periods/{test_species}",
            params={"scenario": "ssp370"}
        )
        resp.raise_for_status()
        data = resp.json()
        
        by_period = data.get("by_period", {})
        print(f"✅ Comparação de períodos:")
        for period, stats in by_period.items():
            print(f"  • {period}: {stats.get('suitable_area_km2', 'N/A'):.2f} km²")
        
        changes = data.get("changes", {})
        if changes:
            print(f"\n  Mudanças (histórico → 2071-2100):")
            print(f"  • {changes.get('hist_to_2071_2100_km2', 'N/A'):.2f} km² ({changes.get('hist_to_2071_2100_pct', 'N/A'):.2f}%)")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_scenarios():
    """Testa análise de cenários climáticos."""
    print_section("6. TESTE: Análise de Cenários Climáticos")
    
    try:
        resp_species = requests.get(f"{BASE_URL}/raster/species")
        species_list = resp_species.json().get("species", [])
        
        if not species_list:
            print("⚠️  Nenhuma espécie encontrada")
            return False
        
        test_species = species_list[0]
        print(f"Testando cenários para: {test_species}\n")
        
        resp = requests.get(f"{BASE_URL}/raster/scenarios/{test_species}")
        resp.raise_for_status()
        data = resp.json()
        
        summary = data.get("summary", {})
        print(f"✅ Análise de cenários:")
        print(f"  • Tendência: {summary.get('trend', 'N/A')}")
        print(f"  • Área mínima: {summary.get('min_area_km2', 'N/A'):.2f} km²")
        print(f"  • Área máxima: {summary.get('max_area_km2', 'N/A'):.2f} km²")
        print(f"  • Área média: {summary.get('avg_area_km2', 'N/A'):.2f} km²")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_overlap():
    """Testa cálculo de sobreposição entre espécies."""
    print_section("7. TESTE: Sobreposição Entre Espécies")
    
    try:
        resp_species = requests.get(f"{BASE_URL}/raster/species")
        species_list = resp_species.json().get("species", [])
        
        if len(species_list) < 2:
            print(f"⚠️  Insuficientes espécies para testar sobreposição ({len(species_list)} encontradas)")
            return False
        
        sp1, sp2 = species_list[0], species_list[1]
        print(f"Testando sobreposição entre: {sp1} e {sp2}\n")
        
        resp = requests.get(
            f"{BASE_URL}/raster/overlap",
            params={
                "species1": sp1,
                "species2": sp2,
                "operation": "intersection"
            }
        )
        resp.raise_for_status()
        data = resp.json()
        
        print(f"✅ Sobreposição calculada:")
        print(f"  • Área de intersecção: {data.get('overlap_km2', 'N/A'):.2f} km²")
        print(f"  • Pixels: {data.get('overlap_pixels', 'N/A')}")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def test_raster_summary():
    """Testa resumo completo de espécie."""
    print_section("8. TESTE: Resumo Completo de Espécie")
    
    try:
        resp_species = requests.get(f"{BASE_URL}/raster/species")
        species_list = resp_species.json().get("species", [])
        
        if not species_list:
            print("⚠️  Nenhuma espécie encontrada")
            return False
        
        test_species = species_list[0]
        print(f"Testando resumo para: {test_species}\n")
        
        resp = requests.get(f"{BASE_URL}/raster/summary/{test_species}")
        resp.raise_for_status()
        data = resp.json()
        
        print(f"✅ Resumo completo obtido:")
        print(f"  • Espécie: {data.get('species')}")
        available = data.get("available_periods", {})
        print(f"  • Períodos disponíveis:")
        for period, available_bool in available.items():
            status = "✓" if available_bool else "✗"
            print(f"    {status} {period}")
        return True
    except Exception as e:
        print(f"❌ Erro: {e}")
        return False

def main():
    """Executa todos os testes."""
    print("\n" + "="*60)
    print("  AlienSmart - Testes da API de Raster")
    print("  Certifique-se que o servidor está rodando em http://localhost:8000")
    print("="*60)
    
    # Verifica se o servidor está acessível
    try:
        resp = requests.get(f"{BASE_URL}/docs")
        print("\n✅ Servidor está acessível!")
    except Exception as e:
        print(f"\n❌ Servidor não está acessível em {BASE_URL}")
        print(f"   Erro: {e}")
        print("   Inicie o servidor com: uvicorn main:app --reload")
        return
    
    # Executa todos os testes
    tests = [
        ("Espécies", test_raster_species),
        ("Ficheiros", test_raster_files),
        ("Parser", test_parse_filename),
        ("Área Adequada", test_suitable_area),
        ("Comparação de Períodos", test_compare_periods),
        ("Cenários", test_scenarios),
        ("Sobreposição", test_overlap),
        ("Resumo Completo", test_raster_summary),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Erro não tratado em {test_name}: {e}")
            results.append((test_name, False))
    
    # Resumo final
    print_section("RESUMO DOS TESTES")
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅" if result else "❌"
        print(f"{status} {test_name}")
    
    print(f"\n📊 {passed}/{total} testes passaram")
    
    if passed == total:
        print("\n🎉 Todos os testes passaram! A API está funcionando corretamente.")
    else:
        print(f"\n⚠️  {total - passed} teste(s) falharam. Verifique os erros acima.")

if __name__ == "__main__":
    main()
